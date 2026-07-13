### Title
Malicious Counterparty Can Steal Escrowed NFTs via Crafted `data.ClassId` in Back-to-Origin Packets — (`x/nft-transfer/keeper/packet.go`)

---

### Summary

In `processReceivedPacket`, when a packet arrives in the back-to-origin direction (`isAwayFromOrigin=false`), the code calls `types.RemoveClassPrefix` to strip the source port/channel prefix from `data.ClassId`, then uses the result to compute `voucherClassID` and unescrow an NFT. `RemoveClassPrefix` is a raw string slice with no validation that the remainder corresponds to a class legitimately sent via this channel. A malicious counterparty chain can craft `data.ClassId` to target any NFT escrowed at the destination escrow address, not just the one originally sent to them.

---

### Finding Description

The vulnerable path is in `processReceivedPacket`, `isAwayFromOrigin=false` branch: [1](#0-0) 

```go
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

`RemoveClassPrefix` is: [2](#0-1) 

```go
func RemoveClassPrefix(portID, channelID, classID string) string {
    classPrefix := GetClassPrefix(portID, channelID)
    return classID[len(classPrefix):]
}
```

This is a blind string slice: `classID[len("sourcePort/sourceChannel/"):]`. It strips exactly that many bytes from the front and returns whatever remains — with no check that the remainder is a path that was legitimately escrowed via this channel.

The `escrowAddress` used for the `TransferOwner` call is: [3](#0-2) 

`GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())` — the escrow for the receiving chain's port/channel.

`ValidateBasic` on the packet data only checks that `classId` is non-blank, token/URI lengths match, and addresses are valid bech32. It does not validate the class path structure or that it corresponds to a legitimately escrowed class: [4](#0-3) 

There is no stored record of which classes were sent via which channel. The only state that exists is the NFT's current owner (the escrow address). The code never checks "was this class actually sent to the counterparty via this channel?"

---

### Impact Explanation

**Attack scenario:**

Suppose chain A has two NFT classes escrowed at `GetEscrowAddress(p1, c1)` (the escrow for the channel to malicious chain B):
- `nftClass` (native, sent directly) — token `tokenA`
- `ibc/HASH_X` (trace: `p3/c3/someClass`, a multi-hop NFT) — token `tokenB`

Malicious chain B sends a crafted back-to-origin packet:
- `sourcePort=p2, sourceChannel=c2, destPort=p1, destChannel=c1`
- `data.ClassId = "p2/c2/p3/c3/someClass"` — starts with `p2/c2/` so `IsAwayFromOrigin` returns `false`
- `data.TokenIds = ["tokenB"]`
- `data.Receiver = attacker_address`

Processing:
1. `IsAwayFromOrigin("p2","c2","p2/c2/p3/c3/someClass")` → `false` ✓ (triggers unescrow branch)
2. `RemoveClassPrefix("p2","c2","p2/c2/p3/c3/someClass")` → `"p3/c3/someClass"`
3. `ParseClassTrace("p3/c3/someClass").IBCClassID()` → `"ibc/HASH_X"`
4. `TransferOwner(ctx, "ibc/HASH_X", "tokenB", escrowAddress, attacker)` → **NFT stolen**

The attacker can steal any NFT owned by the escrow address, not just the ones legitimately sent to them. By observing chain A's state, the attacker can enumerate all NFTs owned by `GetEscrowAddress(p1, c1)` and craft packets to drain them all.

---

### Likelihood Explanation

The attacker must control the counterparty chain (or its validator set), since IBC light clients verify packet commitments. This is a meaningful prerequisite. However, within the IBC threat model, a malicious counterparty is an explicitly considered adversary — the receiving chain is supposed to validate packet data independently. The attack requires no special privileges beyond controlling the counterparty chain, is fully observable (escrow state is public), and is deterministically reproducible.

---

### Recommendation

The receiving chain must verify that the `unprefixedClassID` (after stripping the source prefix) corresponds to a class that was actually sent from this chain to the counterparty via this specific channel. One approach:

1. **Record sent classes per channel**: When escrowing an NFT in `createOutgoingPacket`, store a mapping `(sourcePort, sourceChannel, classID) → true` in the keeper.
2. **Validate on receive**: In the `isAwayFromOrigin=false` branch, after computing `unprefixedClassID`, verify it exists in the sent-classes record for `(destPort, destChannel)`.

Alternatively, validate that `unprefixedClassID` resolves to a class that is currently owned (escrowed) by the escrow address AND that the class trace path does not contain the current channel's port/channel as a prefix (which would indicate it was not sent via this channel).

---

### Proof of Concept

```go
// On malicious chain B, construct and commit a packet:
packetData := types.NonFungibleTokenPacketData{
    // Starts with "p2/c2/" so IsAwayFromOrigin returns false on chain A
    // Remainder "p3/c3/someClass" hashes to ibc/HASH_X — a different escrowed class
    ClassId:   "p2/c2/p3/c3/someClass",
    TokenIds:  []string{"tokenB"},   // tokenB is owned by escrowAddress under ibc/HASH_X
    TokenUris: []string{""},
    Sender:    "cosmos1attacker...",
    Receiver:  "cosmos1attacker...", // attacker's address on chain A
}
// Chain B commits this packet; relayer submits it to chain A.
// chain A's processReceivedPacket:
//   RemoveClassPrefix("p2","c2","p2/c2/p3/c3/someClass") = "p3/c3/someClass"
//   ParseClassTrace("p3/c3/someClass").IBCClassID() = "ibc/HASH_X"
//   TransferOwner(ctx, "ibc/HASH_X", "tokenB", escrowAddr, attacker) → SUCCESS
// Result: attacker receives ibc/HASH_X/tokenB on chain A, stolen from escrow.
```

### Citations

**File:** x/nft-transfer/keeper/packet.go (L151-151)
```go
	escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
```

**File:** x/nft-transfer/keeper/packet.go (L192-201)
```go
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

**File:** x/nft-transfer/types/trace.go (L40-43)
```go
func RemoveClassPrefix(portID, channelID, classID string) string {
	classPrefix := GetClassPrefix(portID, channelID)
	return classID[len(classPrefix):]
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
