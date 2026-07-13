### Title
IBC Voucher NFTs Permanently Non-Transferable On-Chain Due to `ValidateDenomID` in `MsgTransferNFT.ValidateBasic` — (`x/nft/types/msgs.go`)

---

### Summary

`MsgTransferNFT.ValidateBasic()` calls `ValidateDenomID` instead of `ValidateDenomIDWithIBC`. Because IBC voucher NFTs are minted with denom IDs of the form `ibc/<HEX64>`, which contain a `/` character, `ValidateDenomID`'s alphanumeric regex unconditionally rejects them. The message never reaches the keeper. An NFT owner who holds an IBC voucher NFT cannot transfer it to any other address on the same chain via `MsgTransferNFT`.

---

### Finding Description

**Step 1 — IBC voucher NFTs are minted with `ibc/HASH` denom IDs.**

When an NFT packet arrives on the destination chain, `processReceivedPacket` mints the voucher NFT under `voucherClassID`: [1](#0-0) 

`voucherClassID` is always of the form `ibc/<64-char hex>`, e.g. `ibc/27394FB092D2ECCD56123C74F36E4C1F926001CEADA9CA97EA622B25F41E5EB2`.

**Step 2 — `ValidateDenomID` rejects any denom ID containing `/`.** [2](#0-1) 

`IsAlphaNumeric` is `^[a-z0-9]+$` — the `/` in `ibc/...` causes an immediate rejection with `ErrInvalidDenom`.

**Step 3 — `MsgTransferNFT.ValidateBasic` calls `ValidateDenomID`, not `ValidateDenomIDWithIBC`.** [3](#0-2) 

The IBC-aware variant exists and correctly handles the `ibc/` prefix: [4](#0-3) 

But it is never called from `MsgTransferNFT.ValidateBasic`. The same bug affects `MsgEditNFT.ValidateBasic` (line 135) and `MsgBurnNFT.ValidateBasic` (line 221).

**Step 4 — The keeper itself has no such restriction.**

`TransferOwner` in the keeper operates on raw denom IDs from state and has no alphanumeric guard. The block is entirely in `ValidateBasic`, which runs before the message reaches the keeper. [5](#0-4) 

**Step 5 — The only escape is IBC send-back.**

`x/nft-transfer/types/msgs.go` `MsgTransfer.ValidateBasic` only checks that `ClassId` is non-empty: [6](#0-5) 

So the owner can send the voucher back via IBC, but cannot transfer it to any other address on the same chain.

---

### Impact Explanation

Any user who receives an IBC voucher NFT (denom `ibc/<HASH>`) on this chain cannot:
- Transfer it to another address on-chain (`MsgTransferNFT` → `ErrInvalidDenom`)
- Edit its metadata (`MsgEditNFT` → same rejection)
- Burn it (`MsgBurnNFT` → same rejection)

The NFT is effectively locked in the recipient's account for all on-chain operations. The only exit is sending it back via IBC. This violates the invariant that NFT owners can freely transfer their assets.

---

### Likelihood Explanation

This is triggered by any standard IBC NFT receive. No special attacker privileges are needed — any user who receives an IBC voucher NFT hits this immediately upon attempting a normal on-chain transfer. The code path is deterministic and unconditional.

---

### Recommendation

Replace `ValidateDenomID` with `ValidateDenomIDWithIBC` in the `ValidateBasic` methods of `MsgTransferNFT`, `MsgEditNFT`, and `MsgBurnNFT` in `x/nft/types/msgs.go`:

```go
// MsgTransferNFT.ValidateBasic (line 86)
if err := ValidateDenomIDWithIBC(msg.DenomId); err != nil {
    return err
}

// MsgEditNFT.ValidateBasic (line 135)
if err := ValidateDenomIDWithIBC(msg.DenomId); err != nil {
    return err
}

// MsgBurnNFT.ValidateBasic (line 221)
if err := ValidateDenomIDWithIBC(msg.DenomId); err != nil {
    return err
}
```

---

### Proof of Concept

```go
func TestMsgTransferNFT_IBCDenomRejected(t *testing.T) {
    hash64 := "27394FB092D2ECCD56123C74F36E4C1F926001CEADA9CA97EA622B25F41E5EB2"
    ibcDenomID := "ibc/" + hash64

    sender   := sdk.AccAddress(secp256k1.GenPrivKey().PubKey().Address()).String()
    recipient := sdk.AccAddress(secp256k1.GenPrivKey().PubKey().Address()).String()

    msg := types.NewMsgTransferNFT("tokenid1", ibcDenomID, sender, recipient)
    err := msg.ValidateBasic()
    // This FAILS with ErrInvalidDenom, proving IBC voucher NFTs are non-transferable
    require.NoError(t, err) // <-- assertion fails in current code
}
```

Running this against the unmodified codebase will show `ValidateBasic` returning `ErrInvalidDenom` for a legitimately owned IBC voucher NFT.

### Citations

**File:** x/nft-transfer/keeper/packet.go (L165-168)
```go
		voucherClassID := classTrace.IBCClassID()

		if !k.nftKeeper.HasDenomID(ctx, voucherClassID) {
			if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
```

**File:** x/nft/types/validation.go (L30-38)
```go
func ValidateDenomID(denomID string) error {
	if len(denomID) < MinDenomLen || len(denomID) > MaxDenomLen {
		return sdkerrors.Wrapf(ErrInvalidDenom, "the length of denom(%s) only accepts value [%d, %d]", denomID, MinDenomLen, MaxDenomLen)
	}
	if !IsBeginWithAlpha(denomID) || !IsAlphaNumeric(denomID) {
		return sdkerrors.Wrapf(ErrInvalidDenom, "the denom(%s) only accepts lowercase alphanumeric characters, and begin with an english letter", denomID)
	}
	return nil
}
```

**File:** x/nft/types/validation.go (L41-54)
```go
func ValidateDenomIDWithIBC(denomID string) error {
	if strings.HasPrefix(denomID, IBCPrefix) {
		if len(denomID) != IBCDenomLen {
			return sdkerrors.Wrapf(ErrInvalidDenom, "the length of ibc denom(%s) only accepts value [%d]", denomID, IBCDenomLen)
		}
		if _, err := hex.DecodeString(denomID[len(IBCPrefix):]); err != nil {
			return sdkerrors.Wrapf(ErrInvalidDenom, "the hash of ibc denom(%s) must be valid hex", denomID)
		}

		return nil
	}

	return ValidateDenomID(denomID)
}
```

**File:** x/nft/types/msgs.go (L85-88)
```go
func (msg MsgTransferNFT) ValidateBasic() error {
	if err := ValidateDenomID(msg.DenomId); err != nil {
		return err
	}
```

**File:** x/nft/keeper/msg_server.go (L129-146)
```go
func (m msgServer) TransferNFT(goCtx context.Context, msg *types.MsgTransferNFT) (*types.MsgTransferNFTResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	recipient, err := sdk.AccAddressFromBech32(msg.Recipient)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.TransferOwner(ctx, msg.DenomId, msg.Id,
		sender,
		recipient,
	); err != nil {
		return nil, err
	}
```

**File:** x/nft-transfer/types/msgs.go (L65-67)
```go
	if strings.TrimSpace(msg.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}
```
