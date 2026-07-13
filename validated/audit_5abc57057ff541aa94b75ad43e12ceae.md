### Title
Malicious IBC Counterparty Can Steal Escrowed Native NFTs via Crafted Sink-Direction Packet — (`x/nft-transfer/keeper/packet.go`)

---

### Summary

In `processReceivedPacket`, when `isAwayFromOrigin=false` (sink direction), the code removes the source port/channel prefix from `data.ClassId` and calls `ParseClassTrace(unprefixedClassID).IBCClassID()`. If the result has no `/`, `IBCClassID()` returns the raw string (not an `ibc/HASH` form). The code then calls `TransferOwner(escrowAddress, receiver)` with that raw native classID. There is no guard verifying that the resulting classID corresponds to a previously registered class trace or that the NFT being unescrow'd was actually deposited through this channel. A malicious counterparty chain can craft a packet whose `data.ClassId` is `"<sourcePort>/<sourceChannel>/nativeClass"`, causing chain A to unescrow a native NFT it holds in escrow and transfer it to an attacker-controlled receiver.

---

### Finding Description

**Step 1 — Precondition (legitimate escrow):**

A user on chain A sends native NFT `("nativeClass", "tokenX")` to chain B via channel-0 (`sourcePort="nft"`, `sourceChannel="channel-0"`). In `createOutgoingPacket`, `isAwayFromOrigin=true`, so the NFT is transferred to `GetEscrowAddress("nft", "channel-0")`. [1](#0-0) 

**Step 2 — Attacker crafts a return packet:**

The malicious counterparty chain B commits a packet to chain A with:
- `packet.SourcePort = "nft"`, `packet.SourceChannel = "channel-1"` (B's side)
- `packet.DestPort = "nft"`, `packet.DestChannel = "channel-0"` (A's side)
- `data.ClassId = "nft/channel-1/nativeClass"` (crafted)
- `data.TokenIds = ["tokenX"]`
- `data.Receiver = attacker_address`

**Step 3 — `processReceivedPacket` executes the theft:**

`isAwayFromOrigin` is computed as:

```go
IsAwayFromOrigin("nft", "channel-1", "nft/channel-1/nativeClass")
// → strings.HasPrefix("nft/channel-1/nativeClass", "nft/channel-1/") → true → NOT away → false
``` [2](#0-1) 

So the `else` branch executes:

```go
unprefixedClassID := RemoveClassPrefix("nft", "channel-1", "nft/channel-1/nativeClass")
// → "nativeClass"
voucherClassID := ParseClassTrace("nativeClass").IBCClassID()
// → ClassTrace{Path:"", BaseClassId:"nativeClass"}.IBCClassID() → "nativeClass"
``` [3](#0-2) [4](#0-3) 

Then:

```go
escrowAddress := GetEscrowAddress("nft", "channel-0")  // holds "nativeClass/tokenX"
TransferOwner(ctx, "nativeClass", "tokenX", escrowAddress, attacker_address)
``` [5](#0-4) 

`TransferOwner` only checks that `escrowAddress` is the current owner of `("nativeClass", "tokenX")` — which it is, because the legitimate user escrowed it there. The check passes and the NFT is transferred to the attacker. [6](#0-5) 

**Step 4 — Missing guard:**

`OnRecvPacket` calls only `data.ValidateBasic()`, which checks that `ClassId` is non-empty and addresses are valid. It does not validate that `data.ClassId` corresponds to a registered class trace, nor that the NFT being unescrow'd was deposited through this specific channel. [7](#0-6) 

---

### Impact Explanation

Any native NFT escrowed on a channel can be stolen by the counterparty chain of that channel. The attacker does not need to be the original sender. The escrow address is deterministic and public (`GetEscrowAddress(portID, channelID)`), so the attacker can enumerate all escrowed NFTs and target any of them. This is a direct, unauthorized transfer of NFT ownership — a Critical-scope impact. [8](#0-7) 

---

### Likelihood Explanation

The attack requires the counterparty chain to be malicious or compromised. In the IBC threat model, connecting to an untrusted chain should not put native assets at risk. Any channel opened to a chain that later turns malicious (or was always malicious) exposes all currently-escrowed native NFTs on that channel to theft. No governance, key compromise, or social engineering is required — only the ability to commit a packet on the counterparty chain.

---

### Recommendation

In the `else` branch of `processReceivedPacket`, after computing `unprefixedClassID`, verify that if `unprefixedClassID` contains no `/` (i.e., it is a native classID), the `escrowAddress` is the correct escrow for the destination channel — and additionally, require that a class trace record exists for the full path that was originally registered when the NFT was first sent outbound. Concretely:

- Check that `k.HasClassTrace(ctx, classTrace.Hash())` is true before proceeding with unescrow in the sink direction (analogous to the `isAwayFromOrigin` branch which calls `k.HasClassTrace`).
- Or, maintain a per-channel escrow ledger that records which `(classID, tokenID)` pairs were escrowed on which channel, and validate against it on receive. [5](#0-4) 

---

### Proof of Concept

```go
// Keeper test outline (unmodified Go/Cosmos test setup):
// 1. Mint native NFT ("nativeClass", "tokenX") owned by userAddr.
// 2. Call createOutgoingPacket(sourcePort="nft", sourceChannel="channel-0", ..., classID="nativeClass", tokenIDs=["tokenX"], sender=userAddr)
//    → NFT is now owned by GetEscrowAddress("nft", "channel-0")
// 3. Craft a return packet:
//    packet.SourcePort="nft", packet.SourceChannel="channel-1"
//    packet.DestPort="nft",   packet.DestChannel="channel-0"
//    data.ClassId="nft/channel-1/nativeClass"
//    data.TokenIds=["tokenX"]
//    data.Receiver=attackerAddr.String()
// 4. Call keeper.OnRecvPacket(ctx, version, craftedPacket, data)
// 5. Assert: GetNFT(ctx, "nativeClass", "tokenX").GetOwner() == attackerAddr
//    (NFT stolen from escrow without original owner's consent)
```

### Citations

**File:** x/nft-transfer/keeper/packet.go (L106-111)
```go
		if isAwayFromOrigin {
			// create the escrow address for the tokens
			escrowAddress := types.GetEscrowAddress(sourcePort, sourceChannel)
			if err := k.nftKeeper.TransferOwner(ctx, classID, tokenID, sender, escrowAddress); err != nil {
				return channeltypes.Packet{}, err
			}
```

**File:** x/nft-transfer/keeper/packet.go (L186-201)
```go
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

**File:** x/nft-transfer/types/trace.go (L49-52)
```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
	prefixClassID := GetClassPrefix(sourcePort, sourceChannel)
	return !strings.HasPrefix(fullClassPath, prefixClassID)
}
```

**File:** x/nft-transfer/types/trace.go (L61-75)
```go
func ParseClassTrace(rawClassID string) ClassTrace {
	classSplit := strings.Split(rawClassID, "/")

	if classSplit[0] == rawClassID {
		return ClassTrace{
			Path:        "",
			BaseClassId: rawClassID,
		}
	}

	return ClassTrace{
		Path:        strings.Join(classSplit[:len(classSplit)-1], "/"),
		BaseClassId: classSplit[len(classSplit)-1],
	}
}
```

**File:** x/nft-transfer/types/trace.go (L102-107)
```go
func (ct ClassTrace) IBCClassID() string {
	if ct.Path != "" {
		return fmt.Sprintf("%s/%s", ClassPrefix, ct.Hash())
	}
	return ct.BaseClassId
}
```

**File:** x/nft/keeper/keeper.go (L121-138)
```go
func (k Keeper) TransferOwner(
	ctx sdk.Context, denomID, tokenID string, srcOwner, dstOwner sdk.AccAddress,
) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	nft, err := k.IsOwner(ctx, denomID, tokenID, srcOwner)
	if err != nil {
		return err
	}

	nft.Owner = dstOwner.String()

	k.setNFT(ctx, denomID, nft)
	k.swapOwner(ctx, denomID, tokenID, srcOwner, dstOwner)
	return nil
}
```

**File:** x/nft-transfer/keeper/relay.go (L101-111)
```go
func (k Keeper) OnRecvPacket(ctx sdk.Context, channelVersion string, packet channeltypes.Packet,
	data types.NonFungibleTokenPacketData,
) error {
	// validate packet data upon receiving
	if err := data.ValidateBasic(); err != nil {
		return err
	}

	// See spec for this logic: https://github.com/cosmos/ibc/blob/master/spec/app/ics-721-nft-transfer/README.md#packet-relay
	return k.processReceivedPacket(ctx, packet, data)
}
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
