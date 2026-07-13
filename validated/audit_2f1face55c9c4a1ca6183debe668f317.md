### Title
IBC NFT Escrow Permanently Locks NFTs When Both Timeouts Are Disabled and Relayer Fails - (File: x/nft-transfer/keeper/packet.go)

### Summary

The `x/nft-transfer` module allows a user to submit a `MsgTransfer` with both `timeout_height = 0-0` and `timeout_timestamp = 0`, disabling all timeout protection. If the relayer is down or censoring, the NFT is transferred to the escrow address and permanently locked with no admin or user-initiated recovery path.

### Finding Description

In `createOutgoingPacket`, when `isAwayFromOrigin=true` (the source chain case), the NFT is transferred from the sender to the escrow address:

```go
escrowAddress := types.GetEscrowAddress(sourcePort, sourceChannel)
if err := k.nftKeeper.TransferOwner(ctx, classID, tokenID, sender, escrowAddress); err != nil {
    return channeltypes.Packet{}, err
}
```

The escrow address is a deterministic hash-derived address with no private key. The only recovery paths are:

1. A successful `OnAcknowledgementPacket` (packet delivered)
2. `OnTimeoutPacket` → `refundPacketToken` (timeout triggered by a relayer)

Both recovery paths require an active relayer to submit a transaction. If both `timeout_height` and `timeout_timestamp` are set to `0`, the IBC core layer never marks the packet as timed out, so `OnTimeoutPacket` is never called. The CLI explicitly supports this:

```go
cmd.Flags().String(flagPacketTimeoutHeight, "0-0", "Packet timeout block height. The timeout is disabled when set to 0-0.")
cmd.Flags().Uint64(flagPacketTimeoutTimestamp, types.DefaultRelativePacketTimeoutTimestamp, "... The timeout is disabled when set to 0.")
```

A user can pass `--packet-timeout-height=0-0 --packet-timeout-timestamp=0`, disabling both. There is no `ValidateBasic` guard in the module's `MsgTransfer` that rejects a message where both timeouts are zero. There is also no governance proposal, admin function, or emergency withdrawal mechanism to recover NFTs from the escrow address.

### Impact Explanation

NFTs transferred via `MsgTransfer` with both timeouts disabled are permanently locked in the escrow address if the relayer is down, censoring, or the counterparty chain is halted. The NFT owner loses their asset with no on-chain recovery path. The corrupted invariant is: **NFT ownership** — the NFT is owned by the escrow address (a keyless account) indefinitely, and the original owner has no mechanism to reclaim it.

### Likelihood Explanation

A user who sets both timeouts to 0 (e.g., intending a "no-expiry" transfer) and whose relayer subsequently goes offline faces permanent loss. Relayer downtime, censorship, or counterparty chain halts are realistic operational conditions. The CLI default for `timeout_height` is already `0-0` (disabled), meaning only `timeout_timestamp` provides protection by default; a user who also sets `--packet-timeout-timestamp=0` triggers the stuck-funds condition.

### Recommendation

1. Add a `ValidateBasic` check in `MsgTransfer` that rejects messages where both `timeout_height` and `timeout_timestamp` are zero, ensuring at least one timeout is always active.
2. Add an admin or governance-gated emergency withdrawal function that can transfer NFTs out of the escrow address back to their original owners in the event of a permanently stalled channel.

### Proof of Concept

1. User owns NFT with `classID=kitty`, `tokenID=001` on chain A.
2. User submits:
   ```
   chain-maind tx nft-transfer transfer nft channel-0 <receiver> kitty 001 \
     --packet-timeout-height=0-0 \
     --packet-timeout-timestamp=0 \
     --absolute-timeouts
   ```
3. `createOutgoingPacket` executes: NFT ownership is transferred to `escrowAddress = GetEscrowAddress("nft", "channel-0")`.
4. Relayer goes offline; packet is never relayed to the counterparty.
5. Because both timeouts are 0, IBC core never marks the packet as timed out; `OnTimeoutPacket` is never invoked.
6. `refundPacketToken` is never called; the NFT remains owned by the keyless escrow address.
7. No governance or admin message exists to recover the NFT. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** x/nft-transfer/client/cli/tx.go (L104-106)
```go
	cmd.Flags().String(flagPacketTimeoutHeight, "0-0", "Packet timeout block height. The timeout is disabled when set to 0-0.")
	cmd.Flags().Uint64(flagPacketTimeoutTimestamp, types.DefaultRelativePacketTimeoutTimestamp, "Packet timeout timestamp in nanoseconds from now. Default is 10 minutes. The timeout is disabled when set to 0.")
	cmd.Flags().Bool(flagAbsoluteTimeouts, false, "Timeout flags are used as absolute timeouts.")
```

**File:** x/nft-transfer/types/keys.go (L45-55)
```go
func GetEscrowAddress(portID, channelID string) sdk.AccAddress {
	// a slash is used to create domain separation between port and channel identifiers to
	// prevent address collisions between escrow addresses created for different channels
	contents := fmt.Sprintf("%s/%s", portID, channelID)

	// ADR 028 AddressHash construction
	preImage := []byte(Version)
	preImage = append(preImage, 0)
	preImage = append(preImage, contents...)
	hash := sha256.Sum256(preImage)
	return hash[:20]
```

**File:** x/nft-transfer/keeper/relay.go (L128-131)
```go
// OnTimeoutPacket refunds the sender since the original packet sent was
// never received and has been timed out.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, channelVersion string, packet channeltypes.Packet, data types.NonFungibleTokenPacketData) error {
	return k.refundPacketToken(ctx, packet, data)
```
