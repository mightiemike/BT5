### Title
Crafted IBC Packet ClassId Bypasses Direction Check, Allowing Theft of Escrowed NFTs — (`x/nft-transfer/keeper/packet.go`, `x/nft-transfer/types/trace.go`)

---

### Summary

A malicious counterparty chain can send an IBC NFT packet with a `data.ClassId` crafted to begin with its own `sourcePort/sourceChannel/` prefix. This causes `IsAwayFromOrigin` in `processReceivedPacket` to return `false` (sink/return direction) instead of `true` (away-from-origin), triggering the unescrow branch and transferring a legitimately escrowed NFT to the attacker's address.

---

### Finding Description

`IsAwayFromOrigin` determines transfer direction by checking whether `data.ClassId` starts with the packet's source port/channel prefix: [1](#0-0) 

In `processReceivedPacket`, this is called with the **counterparty's** port and channel: [2](#0-1) 

A malicious Chain B (counterparty) can craft `data.ClassId = "nft/channel-1/realClass"` where `nft/channel-1` is Chain B's own source port/channel. This makes `strings.HasPrefix(data.ClassId, "nft/channel-1/")` return `true`, so `IsAwayFromOrigin` returns `false`, and the `else` (unescrow) branch executes: [3](#0-2) 

The `unprefixedClassID` becomes `"realClass"` (after stripping the crafted prefix), `voucherClassID` resolves to `"realClass"` (since `IBCClassID()` returns the base class ID when path is empty), and `escrowAddress` is `GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())` — which is exactly the escrow address where the legitimate user's NFT was locked: [4](#0-3) 

`TransferOwner` then succeeds because the escrow address is the actual owner of the NFT: [5](#0-4) 

`ValidateBasic` on the packet data performs no format validation on `ClassId` — it only checks non-emptiness — so the crafted value passes without error: [6](#0-5) 

---

### Impact Explanation

A malicious counterparty chain can steal any NFT that has been escrowed on Chain A via a prior legitimate IBC send. The NFT ownership record is permanently changed from the escrow address to the attacker's address. The original sender loses their NFT with no recourse. This is a direct, irreversible theft of user-owned NFTs.

---

### Likelihood Explanation

The attack requires a malicious or compromised counterparty chain, which is a realistic threat model for IBC (chains are independent trust domains). No privileged access on Chain A is needed. The crafted packet is valid JSON that passes all on-chain validation. The attack is reproducible in a standard Go/Cosmos test environment with two chains sharing a channel.

---

### Recommendation

In `processReceivedPacket`, validate that `data.ClassId` does **not** start with the destination chain's own port/channel prefix before accepting it as a legitimate return-to-origin transfer. Specifically, reject any inbound packet whose `ClassId` begins with `GetClassPrefix(packet.GetDestPort(), packet.GetDestChannel())`, as that would indicate the counterparty is spoofing a return path that was never legitimately established. Additionally, `ValidateBasic` on `NonFungibleTokenPacketData` should validate the `ClassId` format against the ICS-721 trace path rules.

---

### Proof of Concept

**State setup on Chain A** (port `nft`, channel `channel-0` ↔ Chain B port `nft`, channel `channel-1`):

1. User calls `MsgTransfer` on Chain A: `classID="realClass"`, `tokenID="token1"`, via `(nft, channel-0)`.
2. `createOutgoingPacket` calls `IsAwayFromOrigin("nft", "channel-0", "realClass")` → `true` → NFT escrowed at `GetEscrowAddress("nft", "channel-0")`.

**Attack from Chain B**:

3. Chain B constructs a packet with:
   - `SourcePort="nft"`, `SourceChannel="channel-1"`, `DestPort="nft"`, `DestChannel="channel-0"`
   - `data.ClassId = "nft/channel-1/realClass"` (crafted)
   - `data.TokenIds = ["token1"]`
   - `data.Receiver = attacker_bech32_address`

4. Chain A's `OnRecvPacket` → `OnRecvPacket` keeper → `processReceivedPacket`:
   - `IsAwayFromOrigin("nft", "channel-1", "nft/channel-1/realClass")` → `HasPrefix("nft/channel-1/realClass", "nft/channel-1/")` = `true` → returns `false`
   - `unprefixedClassID = RemoveClassPrefix("nft", "channel-1", "nft/channel-1/realClass")` = `"realClass"`
   - `voucherClassID = ParseClassTrace("realClass").IBCClassID()` = `"realClass"`
   - `escrowAddress = GetEscrowAddress("nft", "channel-0")` (the address holding the escrowed NFT)
   - `TransferOwner(ctx, "realClass", "token1", escrowAddress, attacker)` → **succeeds**, NFT transferred to attacker

5. Assert: `GetNFT("realClass", "token1").Owner == attacker_address` ✓ — NFT stolen.

### Citations

**File:** x/nft-transfer/types/trace.go (L49-52)
```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
	prefixClassID := GetClassPrefix(sourcePort, sourceChannel)
	return !strings.HasPrefix(fullClassPath, prefixClassID)
}
```

**File:** x/nft-transfer/keeper/packet.go (L148-148)
```go
	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)
```

**File:** x/nft-transfer/keeper/packet.go (L151-151)
```go
	escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
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

**File:** x/nft-transfer/types/packet.go (L41-44)
```go
func (nftpd NonFungibleTokenPacketData) ValidateBasic() error {
	if strings.TrimSpace(nftpd.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}
```
