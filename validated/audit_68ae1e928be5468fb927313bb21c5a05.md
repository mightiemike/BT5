### Title
Malicious IBC Counterparty Can Steal Escrowed NFTs via Crafted ClassId in `processReceivedPacket` — (`x/nft-transfer/keeper/packet.go`)

---

### Summary

`processReceivedPacket` determines whether to mint or unescrow an NFT solely by calling `IsAwayFromOrigin(packet.SourcePort, packet.SourceChannel, data.ClassId)`. Because `data.ClassId` is fully attacker-controlled packet data and `IsAwayFromOrigin` simply checks whether `data.ClassId` starts with `sourcePort/sourceChannel/`, a malicious IBC counterparty can always force the unescrow branch by prefixing `data.ClassId` with its own source port/channel. This allows it to drain any NFT legitimately escrowed on the destination chain.

---

### Finding Description

`IsAwayFromOrigin` is defined as:

```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
    prefixClassID := GetClassPrefix(sourcePort, sourceChannel) // "nft/channel-X/"
    return !strings.HasPrefix(fullClassPath, prefixClassID)
}
``` [1](#0-0) 

In `processReceivedPacket`, this is called with the **source** (attacker-controlled) port/channel and the **attacker-controlled** `data.ClassId`:

```go
isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)
``` [2](#0-1) 

The attacker simply sets `data.ClassId = sourcePort + "/" + sourceChannel + "/" + targetBaseClass` (e.g., `"nft/channel-1/nativeClass"` where `channel-1` is the malicious chain's own channel). `IsAwayFromOrigin` then returns `false`, and the else-branch executes:

```go
unprefixedClassID := types.RemoveClassPrefix(packet.GetSourcePort(),
    packet.GetSourceChannel(), data.ClassId)
// "nft/channel-1/nativeClass" → "nativeClass"

voucherClassID := types.ParseClassTrace(unprefixedClassID).IBCClassID()
// ParseClassTrace("nativeClass") → ClassTrace{Path:"", BaseClassId:"nativeClass"}
// IBCClassID() → "nativeClass"

for _, tokenID := range data.TokenIds {
    if err := k.nftKeeper.TransferOwner(ctx,
        voucherClassID, tokenID, escrowAddress, receiver); err != nil {
        return err
    }
}
``` [3](#0-2) 

The `escrowAddress` is derived from the **destination** port/channel:

```go
escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
``` [4](#0-3) 

This is exactly the escrow address where legitimate outbound transfers from the destination chain deposit NFTs. If any user on the destination chain previously sent `"nativeClass"/"token1"` outbound through `("nft", "channel-0")`, that NFT sits at `GetEscrowAddress("nft", "channel-0")`. The attacker's crafted packet transfers it to the attacker's `receiver` address.

`ValidateBasic` performs no check that `data.ClassId` represents a legitimate return path — it only checks that the field is non-empty and that addresses are valid: [5](#0-4) 

---

### Impact Explanation

Any NFT escrowed on the destination chain via a legitimate outbound IBC transfer can be stolen. The attacker needs only to:
1. Know the base class ID and token ID of an escrowed NFT (observable on-chain).
2. Control a connected IBC counterparty (any chain with an open channel to the victim chain).
3. Send a crafted `OnRecvPacket` with `data.ClassId = "<srcPort>/<srcChannel>/<targetBaseClass>"`.

This directly violates the invariant that unescrow must only occur for NFTs previously sent back by the legitimate holder on the counterparty chain.

---

### Likelihood Explanation

Any operator of a connected IBC chain (or anyone who can submit IBC packets through a relayer on a permissionless channel) can execute this. The attack requires no governance, no privileged keys, and no code modification — only the ability to submit a valid IBC packet with crafted `data.ClassId`. The escrowed NFT state is publicly visible.

---

### Recommendation

The destination chain must verify that the incoming `data.ClassId` was actually prefixed by the **destination** chain's own port/channel (not the source's) before taking the unescrow path. Specifically, `processReceivedPacket` should check that `data.ClassId` starts with `destPort/destChannel/` before treating the packet as a return-to-origin transfer:

```go
// Correct guard: the packet is "returning home" only if the classId
// was prefixed by THIS chain's own port/channel when it was sent out.
destPrefix := types.GetClassPrefix(packet.GetDestPort(), packet.GetDestChannel())
isAwayFromOrigin := !strings.HasPrefix(data.ClassId, destPrefix)
```

This mirrors how ICS-20 fungible token transfer validates the direction: the receiving chain checks whether the incoming denom is prefixed with its own port/channel, not the sender's.

---

### Proof of Concept

```
Setup:
  - Chain B has port "nft", channel "channel-0" (connected to malicious Chain A, channel "channel-1")
  - Legitimate user on Chain B sends NFT (classId="nativeClass", tokenId="token1") outbound
    → NFT is escrowed at GetEscrowAddress("nft", "channel-0") on Chain B

Attack:
  - Chain A (malicious) sends IBC packet to Chain B:
      packet.SourcePort    = "nft"
      packet.SourceChannel = "channel-1"   ← Chain A's own channel
      packet.DestPort      = "nft"
      packet.DestChannel   = "channel-0"
      data.ClassId         = "nft/channel-1/nativeClass"  ← crafted to start with src prefix
      data.TokenIds        = ["token1"]
      data.Receiver        = attacker_address

  - OnRecvPacket → processReceivedPacket:
      IsAwayFromOrigin("nft", "channel-1", "nft/channel-1/nativeClass")
        = !HasPrefix("nft/channel-1/nativeClass", "nft/channel-1/")
        = !true = false   ← unescrow branch taken

      unprefixedClassID = RemoveClassPrefix("nft","channel-1","nft/channel-1/nativeClass")
                        = "nativeClass"
      voucherClassID    = "nativeClass"
      escrowAddress     = GetEscrowAddress("nft", "channel-0")  ← holds the real NFT

      TransferOwner("nativeClass", "token1", escrowAddress, attacker_address)
        → NFT stolen ✓
```

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

**File:** x/nft-transfer/types/packet.go (L41-72)
```go
func (nftpd NonFungibleTokenPacketData) ValidateBasic() error {
	if strings.TrimSpace(nftpd.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}

	if len(nftpd.TokenIds) == 0 {
		return newsdkerrors.Wrap(ErrInvalidTokenID, "tokenId cannot be blank")
	}

	if len(nftpd.TokenIds) != len(nftpd.TokenUris) {
		return newsdkerrors.Wrap(ErrInvalidPacket, "tokenIds and tokenUris lengths do not match")
	}

	if strings.TrimSpace(nftpd.Sender) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "sender address cannot be blank")
	}

	// decode the sender address
	if _, err := sdk.AccAddressFromBech32(nftpd.Sender); err != nil {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid sender address")
	}

	if strings.TrimSpace(nftpd.Receiver) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "receiver address cannot be blank")
	}

	// decode the receiver address
	if _, err := sdk.AccAddressFromBech32(nftpd.Receiver); err != nil {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid receiver address")
	}
	return nil
}
```
