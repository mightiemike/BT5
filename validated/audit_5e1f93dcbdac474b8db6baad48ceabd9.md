### Title
Escrowed NFTs Permanently Stuck When IBC Channel Closes via `OnChanCloseConfirm` - (File: `x/nft-transfer/ibc_module.go`)

---

### Summary

The `x/nft-transfer` module escrows NFTs into a per-channel escrow address when a user sends an NFT cross-chain. If the IBC channel is subsequently closed via the counterparty-initiated path (`OnChanCloseConfirm`), the module performs no recovery action — all NFTs held in that channel's escrow address are permanently irrecoverable.

---

### Finding Description

When a user sends an NFT cross-chain via `SendTransfer`, the `createOutgoingPacket` function transfers ownership of the NFT to the channel's escrow address (derived deterministically from port and channel IDs): [1](#0-0) 

This escrow address is a normal module-managed account with no privileged withdrawal capability. NFTs held there can only be released by two IBC lifecycle callbacks: `refundPacketToken` (on timeout or error ack) and `processReceivedPacket` (on successful receive in the return direction).

The module explicitly blocks user-initiated channel closure: [2](#0-1) 

However, `OnChanCloseConfirm` — triggered when the **counterparty chain** initiates and confirms a channel close — is a no-op: [3](#0-2) 

Once `OnChanCloseConfirm` executes, the channel is permanently closed. No further packets can be sent or received on it. The escrow address still holds all NFTs that were in-flight or escrowed for that channel, but there is no code path that releases them back to their original owners. The escrow address has no admin, no governance hook, and no sweep function.

The escrow address itself is set up as a plain account with no special permissions: [4](#0-3) 

---

### Impact Explanation

Any NFT that was escrowed on this chain for the closed channel — i.e., NFTs sent away from this chain that had not yet been acknowledged, timed out, or returned — becomes permanently locked in the escrow address. The original owner loses the NFT with no recourse. The NFT cannot be transferred, burned, or recovered through any supported transaction type.

The corrupted invariant: the escrow address holds NFTs whose ownership records in the NFT keeper (`x/nft`) show the escrow address as owner, but no message or IBC callback can ever transfer them out after channel closure.

---

### Likelihood Explanation

The trigger requires a counterparty chain to submit `MsgChannelCloseInit` on its side. This is realistic because:

1. ICS-721 is an open standard; counterparty chains may run different implementations that permit `OnChanCloseInit`.
2. A counterparty chain governance proposal or a chain halt/upgrade could result in channel closure.
3. The IBC protocol itself does not prevent a counterparty from closing a channel even if this chain's module blocks its own `OnChanCloseInit`.

The attacker-controlled entry path is: submit `MsgChannelCloseInit` on the counterparty chain → IBC relayer submits `MsgChannelCloseConfirm` on this chain → `OnChanCloseConfirm` is called → escrowed NFTs are permanently stuck.

---

### Recommendation

Implement `OnChanCloseConfirm` to iterate over all NFTs currently owned by the escrow address for the closing channel and transfer them back to their original owners (or to a governance-controlled recovery address). This mirrors the pattern used by `refundPacketToken`: [5](#0-4) 

Alternatively, if channel closure should never be permitted from either side, `OnChanCloseConfirm` should return an error, consistent with `OnChanCloseInit`.

---

### Proof of Concept

1. User on this chain calls `MsgTransfer` for NFT `tokenA` in class `classX` via channel `channel-0`. `createOutgoingPacket` transfers `tokenA` ownership to `escrowAddress = GetEscrowAddress("nft", "channel-0")`.
2. The IBC packet is in-flight (not yet acknowledged or timed out).
3. The counterparty chain submits `MsgChannelCloseInit` for its end of the channel.
4. A relayer submits `MsgChannelCloseConfirm` on this chain.
5. IBC core calls `IBCModule.OnChanCloseConfirm(ctx, "nft", "channel-0")`.
6. The function returns `nil` immediately with no state changes.
7. `tokenA` remains owned by `escrowAddress` in the NFT keeper. No message (`MsgTransferNFT`, `MsgBurnNFT`, etc.) accepts the escrow address as a signer. No IBC packet can be sent on the now-closed channel. `tokenA` is permanently irrecoverable. [3](#0-2) [1](#0-0)

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

**File:** x/nft-transfer/keeper/packet.go (L106-111)
```go
		if isAwayFromOrigin {
			// create the escrow address for the tokens
			escrowAddress := types.GetEscrowAddress(sourcePort, sourceChannel)
			if err := k.nftKeeper.TransferOwner(ctx, classID, tokenID, sender, escrowAddress); err != nil {
				return channeltypes.Packet{}, err
			}
```

**File:** x/nft-transfer/ibc_module.go (L132-139)
```go
func (im IBCModule) OnChanCloseInit(
	ctx sdk.Context,
	portID,
	channelID string,
) error {
	// Disallow user-initiated channel closing for transfer channels
	return newsdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "user cannot close channel")
}
```

**File:** x/nft-transfer/ibc_module.go (L142-148)
```go
func (im IBCModule) OnChanCloseConfirm(
	ctx sdk.Context,
	portID,
	channelID string,
) error {
	return nil
}
```

**File:** x/nft-transfer/keeper/keeper.go (L62-68)
```go
func (k Keeper) SetEscrowAddress(ctx sdk.Context, portID, channelID string) {
	// create the escrow address for the tokens
	escrowAddress := types.GetEscrowAddress(portID, channelID)
	if !k.authKeeper.HasAccount(ctx, escrowAddress) {
		acc := k.authKeeper.NewAccountWithAddress(ctx, escrowAddress)
		k.authKeeper.SetAccount(ctx, acc)
	}
```
