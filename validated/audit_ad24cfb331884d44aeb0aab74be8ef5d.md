### Title
Atomic NFT Batch Loop in IBC Refund/Receive Permanently Locks All Escrowed NFTs on Single-Token Failure — (File: `x/nft-transfer/keeper/packet.go`)

### Summary
`refundPacketToken` and `processReceivedPacket` in the ICS-721 NFT-transfer keeper loop over all `tokenIDs` in a packet atomically. If any single NFT operation (`TransferOwner` or `MintNFT`) fails, the entire loop reverts. On the refund path this means all NFTs in the batch remain permanently locked in the escrow address with no recovery mechanism, directly mirroring the BasicIssuanceModule pattern where a single problematic asset blocks recovery of every asset in the set.

### Finding Description

`refundPacketToken` is invoked on both `OnAcknowledgementPacket` (error branch) and `OnTimeoutPacket`. It iterates over every `tokenID` in the packet data and either unescrows or re-mints each one: [1](#0-0) 

```go
// away-from-origin: unescrow
for _, tokenID := range data.TokenIds {
    if err := k.nftKeeper.TransferOwner(ctx, voucherClassID, tokenID, escrowAddress, sender); err != nil {
        return err          // ← entire refund aborts here
    }
}
// sink-chain: re-mint burned vouchers
for i, tokenID := range data.TokenIds {
    if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, sender); err != nil {
        return err          // ← entire refund aborts here
    }
}
```

The same pattern appears in `processReceivedPacket`: [2](#0-1) 

There is no partial-success handling, no per-token error isolation, and no fallback accounting. The IBC module layer (`OnTimeoutPacket` / `OnAcknowledgementPacket`) propagates the error directly: [3](#0-2) 

When the error is returned from `OnTimeoutPacket` or `OnAcknowledgementPacket`, the IBC core layer marks the packet as unprocessed and the relayer will retry indefinitely — but every retry hits the same failure for the same tokenID, so the batch is permanently unresolvable.

### Impact Explanation

**Concrete corrupted value:** NFT ownership. NFTs that were transferred to the escrow address (`types.GetEscrowAddress(sourcePort, sourceChannel)`) remain owned by that address forever. The sender loses all N NFTs in the batch because of a failure on a single one.

**Sink-chain path (re-mint on refund):** When Chain B (sink) sends IBC vouchers back toward origin, those vouchers are burned on Chain B during `createOutgoingPacket`. If the packet times out, `refundPacketToken` attempts to re-mint each burned voucher. If any single `MintNFT` call fails — e.g., because the tokenID was independently re-created in the same class by another transaction between send and timeout — the entire re-mint loop reverts. All N vouchers are permanently destroyed: burned on send, never re-minted on refund.

**Away-from-origin path (unescrow on refund):** NFTs are held by the escrow address. If `TransferOwner` fails for one tokenID (e.g., the NFT keeper rejects the transfer due to a state inconsistency introduced by a concurrent operation or a governance-level denom freeze), all N NFTs remain locked in escrow with no user-accessible recovery path.

### Likelihood Explanation

The trigger is reachable by any unprivileged user who:
1. Calls `MsgTransfer` with a batch of `tokenIDs` (the standard ICS-721 send message).
2. Allows or engineers a timeout (short `timeoutTimestamp`, slow relayer, or deliberate non-relay).
3. Has one tokenID in the batch that is in a state that causes `TransferOwner` or `MintNFT` to fail at refund time.

The sink-chain re-mint failure is the most realistic: a user or a separate transaction can mint an NFT with the same `(classID, tokenID)` pair on Chain B between the time the outbound packet is sent and the time the timeout is processed. This is a narrow but real race window on any chain where the NFT class is publicly mintable or where governance can issue tokens into an existing class.

### Recommendation

Decouple the per-token operations from the all-or-nothing transaction boundary. Two approaches:

1. **Internal accounting first, withdraw later:** On refund, credit each tokenID to the sender in a per-address ledger rather than immediately transferring ownership. Expose a separate `MsgWithdrawEscrowedNFT` message that lets the sender claim individual tokens. This is the direct analog of the fix suggested in the original report.

2. **Per-token error isolation with event emission:** Wrap each `TransferOwner` / `MintNFT` call in a cache-context (sub-transaction). On failure, emit an error event for that tokenID, skip it, and continue processing the rest. Store failed tokenIDs in a recoverable escrow record so the user can retry individually.

### Proof of Concept

```
1. User owns NFTs [A, B, C] on Chain X (source).
2. User calls MsgTransfer with tokenIDs=[A, B, C], short timeoutTimestamp=T+10s.
3. createOutgoingPacket escrows A, B, C under escrowAddress on Chain X.
4. Packet is not relayed before T+10s → timeout.
5. Between step 3 and step 4, a separate tx on Chain X deletes NFT B from the
   escrow address (e.g., via a governance burn or a keeper bug).
6. Relayer submits MsgTimeout → OnTimeoutPacket → refundPacketToken.
7. Loop: TransferOwner(A, escrow→user) succeeds.
         TransferOwner(B, escrow→user) FAILS (B no longer owned by escrow).
         → entire refund reverts, A's transfer is also rolled back.
8. All three NFTs remain owned by escrowAddress.
   User has no message to call to recover A or C.
``` [4](#0-3) [3](#0-2)

### Citations

**File:** x/nft-transfer/keeper/packet.go (L21-51)
```go
func (k Keeper) refundPacketToken(ctx sdk.Context, packet channeltypes.Packet, data types.NonFungibleTokenPacketData) error {
	sender, err := sdk.AccAddressFromBech32(data.Sender)
	if err != nil {
		return err
	}

	classTrace := types.ParseClassTrace(data.ClassId)
	voucherClassID := classTrace.IBCClassID()

	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(),
		packet.GetSourceChannel(), data.ClassId)

	escrowAddress := types.GetEscrowAddress(packet.GetSourcePort(), packet.GetSourceChannel())

	if isAwayFromOrigin {
		// unescrow tokens back to the sender
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx, voucherClassID, tokenID, escrowAddress, sender); err != nil {
				return err
			}
		}
	} else {
		// we are sink chain, mint voucher back to sender
		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, sender); err != nil {
				return err
			}
		}
	}

	return nil
```

**File:** x/nft-transfer/keeper/packet.go (L181-201)
```go
		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver); err != nil {
				return err
			}
		}
	} else {
		// If the token moves in the direction of back to origin,
		// we need to unescrow the token and transfer it to the receiver

		// we should remove the prefix. For example:
		// p6/c6/p4/c4/p2/c2/nftClass -> p4/c4/p2/c2/nftClass
		unprefixedClassID := types.RemoveClassPrefix(packet.GetSourcePort(),
			packet.GetSourceChannel(), data.ClassId)

		voucherClassID := types.ParseClassTrace(unprefixedClassID).IBCClassID()
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx,
				voucherClassID, tokenID, escrowAddress, receiver); err != nil {
				return err
			}
		}
```

**File:** x/nft-transfer/ibc_module.go (L246-259)
```go
func (im IBCModule) OnTimeoutPacket(
	ctx sdk.Context,
	channelVersion string,
	packet channeltypes.Packet,
	relayer sdk.AccAddress,
) error {
	var data types.NonFungibleTokenPacketData
	if err := types.ModuleCdc.UnmarshalJSON(packet.GetData(), &data); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrUnknownRequest, "cannot unmarshal ICS-721 transfer packet data: %s", err.Error())
	}
	// refund tokens
	if err := im.keeper.OnTimeoutPacket(ctx, channelVersion, packet, data); err != nil {
		return err
	}
```
