### Title
Missing Destination-Prefix Guard in `processReceivedPacket` Allows Malicious Counterparty to Permanently Corrupt NFT Class Traces — (`x/nft-transfer/keeper/packet.go`)

---

### Summary

`processReceivedPacket` unconditionally prepends the destination port/channel prefix to `data.ClassId` on the away-from-origin path without first checking whether `data.ClassId` already carries that prefix. A malicious IBC counterparty chain can craft a packet whose `ClassId` is pre-prefixed with the receiving chain's own destination prefix, causing a double-prefix to be stored as a valid `ClassTrace`. The resulting `voucherClassID` is permanently wrong and can never be correctly unwound.

---

### Finding Description

**Entry point**: `IBCModule.OnRecvPacket` → `keeper.OnRecvPacket` → `processReceivedPacket`.

**Step 1 — `isAwayFromOrigin` check passes.**

`IsAwayFromOrigin` checks whether `data.ClassId` starts with `sourcePort/sourceChannel/`: [1](#0-0) 

If the attacker sends `data.ClassId = "nft/channel-0/nftClass"` (destination prefix) while the source port/channel is `nft/channel-1` (a different channel), the prefix check is `strings.HasPrefix("nft/channel-0/nftClass", "nft/channel-1/")` → `false`, so `isAwayFromOrigin = true`. The away-from-origin branch is taken.

**Step 2 — Unconditional double-prefix.** [2](#0-1) 

`classPrefix = "nft/channel-0/"`, so:
```
prefixedClassID = "nft/channel-0/" + "nft/channel-0/nftClass"
               = "nft/channel-0/nft/channel-0/nftClass"
```

**Step 3 — `ParseClassTrace` produces a malformed but structurally valid trace.** [3](#0-2) 

`classSplit = ["nft","channel-0","nft","channel-0","nftClass"]` → `ClassTrace{Path:"nft/channel-0/nft/channel-0", BaseClassId:"nftClass"}`.

**Step 4 — No `Validate()` call before `SetClassTrace`.** [4](#0-3) 

`SetClassTrace` stores the malformed trace directly with no validation: [5](#0-4) 

**Step 5 — Even if `Validate()` were called, it would pass.**

`validateTraceIdentifiers` only checks that the path splits into an even number of valid port/channel pairs: [6](#0-5) 

`["nft","channel-0","nft","channel-0"]` — 4 identifiers, all valid — passes without error.

**Step 6 — `ValidateBasic` does not catch this.** [7](#0-6) 

`ValidateBasic` only checks that `ClassId` is non-blank and that addresses are valid bech32. No check that `ClassId` does not already carry the destination prefix.

**Step 7 — NFT minted under permanently wrong `voucherClassID`.** [8](#0-7) 

`voucherClassID = ibc/sha256("nft/channel-0/nft/channel-0/nftClass")` instead of the correct `ibc/sha256("nft/channel-0/nftClass")`.

---

### Impact Explanation

When the user on Chain B attempts to send the NFT back:

- `fullClassPath = "nft/channel-0/nft/channel-0/nftClass"` (retrieved from the stored malformed trace)
- `IsAwayFromOrigin("nft","channel-0","nft/channel-0/nft/channel-0/nftClass")` → `false` (sink chain path taken)
- Chain B burns the NFT and sends a packet with `ClassId = "nft/channel-0/nft/channel-0/nftClass"`
- Chain A receives this and computes `unprefixedClassID = "nft/channel-0/nftClass"` → `voucherClassID = ibc/sha256("nft/channel-0/nftClass")`, which does not correspond to the original escrowed NFT class on Chain A
- `TransferOwner` fails or unescrows the wrong NFT; the original escrowed NFT is permanently locked

The NFT minted on Chain B is permanently stuck under a wrong class ID. The original NFT on Chain A (if escrowed by a legitimate user who trusted the malicious chain) is permanently locked.

---

### Likelihood Explanation

The attacker must control a counterparty chain (or a chain that has an established IBC channel with the victim chain). This is a standard IBC threat model — the IBC protocol is explicitly designed to protect receiving chains against malicious counterparties. The attack requires no governance access, no private key compromise, and no social engineering on the victim chain. Any chain that opens an IBC channel with the victim chain can execute this attack. The IBC NFT packet lifecycle is an explicitly listed supported production path in the scope.

---

### Recommendation

In `processReceivedPacket`, before prepending the destination prefix, check that `data.ClassId` does not already start with the destination prefix:

```go
classPrefix := types.GetClassPrefix(packet.GetDestPort(), packet.GetDestChannel())
if strings.HasPrefix(data.ClassId, classPrefix) {
    return sdkerrors.Wrapf(types.ErrInvalidClassID,
        "classId %s already contains destination prefix %s", data.ClassId, classPrefix)
}
prefixedClassID := classPrefix + data.ClassId
```

Additionally, call `classTrace.Validate()` after `ParseClassTrace` and before `SetClassTrace` as a defense-in-depth measure, even though the malformed trace passes the current `validateTraceIdentifiers` check.

---

### Proof of Concept

```
Setup:
  Chain A (malicious counterparty): port=nft, channel=channel-1 (its side)
  Chain B (victim):                 port=nft, channel=channel-0 (its side)

Attack:
  Chain A sends IBC packet with:
    data.ClassId  = "nft/channel-0/nftClass"   // pre-prefixed with Chain B's dest prefix
    data.TokenIds = ["token-1"]
    data.Sender   = <valid bech32>
    data.Receiver = <victim user on Chain B>

Chain B processReceivedPacket:
  isAwayFromOrigin("nft","channel-1","nft/channel-0/nftClass") = true
  classPrefix      = "nft/channel-0/"
  prefixedClassID  = "nft/channel-0/nft/channel-0/nftClass"   // double-prefixed
  classTrace       = {Path:"nft/channel-0/nft/channel-0", BaseClassId:"nftClass"}
  SetClassTrace(classTrace)                                    // stored, no error
  voucherClassID   = ibc/sha256("nft/channel-0/nft/channel-0/nftClass")
  MintNFT(voucherClassID, "token-1", ...)                     // minted under wrong ID

Send-back attempt from Chain B:
  fullClassPath    = "nft/channel-0/nft/channel-0/nftClass"
  isAwayFromOrigin("nft","channel-0","nft/channel-0/nft/channel-0/nftClass") = false
  BurnNFTUnverified(voucherClassID, "token-1")
  packet.ClassId   = "nft/channel-0/nft/channel-0/nftClass"

Chain A receives send-back:
  unprefixedClassID = "nft/channel-0/nftClass"
  voucherClassID    = ibc/sha256("nft/channel-0/nftClass")    // wrong class, not escrowed
  TransferOwner fails or transfers wrong NFT
  Original NFT permanently locked in escrow
```

### Citations

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

**File:** x/nft-transfer/types/trace.go (L125-139)
```go
func validateTraceIdentifiers(identifiers []string) error {
	if len(identifiers) == 0 || len(identifiers)%2 != 0 {
		return fmt.Errorf("trace info must come in pairs of port and channel identifiers '{portID}/{channelID}', got the identifiers: %s", identifiers)
	}

	// validate correctness of port and channel identifiers
	for i := 0; i < len(identifiers); i += 2 {
		if err := host.PortIdentifierValidator(identifiers[i]); err != nil {
			return sdkerrors.Wrapf(err, "invalid port ID at position %d", i)
		}
		if err := host.ChannelIdentifierValidator(identifiers[i+1]); err != nil {
			return sdkerrors.Wrapf(err, "invalid channel ID at position %d", i)
		}
	}
	return nil
```

**File:** x/nft-transfer/keeper/packet.go (L154-163)
```go
		// since SendPacket did not prefix the classID, we must prefix classID here
		classPrefix := types.GetClassPrefix(packet.GetDestPort(), packet.GetDestChannel())
		// NOTE: sourcePrefix contains the trailing "/"
		prefixedClassID := classPrefix + data.ClassId

		// construct the class trace from the full raw classID
		classTrace := types.ParseClassTrace(prefixedClassID)
		if !k.HasClassTrace(ctx, classTrace.Hash()) {
			k.SetClassTrace(ctx, classTrace)
		}
```

**File:** x/nft-transfer/keeper/packet.go (L165-185)
```go
		voucherClassID := classTrace.IBCClassID()

		if !k.nftKeeper.HasDenomID(ctx, voucherClassID) {
			if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
				return err
			}
		}
		sdkCtx := sdk.UnwrapSDKContext(ctx)
		sdkCtx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeClassTrace,
				sdk.NewAttribute(types.AttributeKeyTraceHash, classTrace.Hash().String()),
				sdk.NewAttribute(types.AttributeKeyClassID, voucherClassID),
			),
		)

		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver); err != nil {
				return err
			}
		}
```

**File:** x/nft-transfer/keeper/trace.go (L76-80)
```go
func (k Keeper) SetClassTrace(ctx sdk.Context, denomTrace types.ClassTrace) {
	store := prefix.NewStore(ctx.KVStore(k.storeKey), types.ClassTraceKey)
	bz := k.MustMarshalClassTrace(denomTrace)
	store.Set(denomTrace.Hash(), bz)
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
