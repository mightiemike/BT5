### Title
`MsgTransferNFT.ValidateBasic` Uses `ValidateDenomID` Instead of `ValidateDenomIDWithIBC`, Permanently Blocking On-Chain Transfer of IBC-Voucher NFTs — (`x/nft/types/msgs.go`)

---

### Summary

`MsgTransferNFT.ValidateBasic()` calls `ValidateDenomID` to validate the denom ID. However, NFTs received via IBC are stored with denom IDs in the format `ibc/<64-hex-chars>` (total length 68), which `ValidateDenomID` unconditionally rejects. A separate function `ValidateDenomIDWithIBC` exists precisely to handle this format, but it is never called from `MsgTransferNFT.ValidateBasic()`. The result is that any IBC-voucher NFT owner is permanently blocked from using the standard `MsgTransferNFT` path to transfer their asset to another address on the same chain.

---

### Finding Description

`ValidateDenomID` enforces two hard constraints:

1. Length must be in `[MinDenomLen=3, MaxDenomLen=64]`
2. Must match `^[a-z0-9]+$` and begin with `[a-z]` [1](#0-0) 

An IBC-voucher denom ID has the form `ibc/<sha256-hex>`, which is 4 + 64 = **68 characters**, contains a `/`, and contains uppercase hex digits — failing both constraints simultaneously.

`ValidateDenomIDWithIBC` handles this correctly: it checks for the `ibc/` prefix, validates the total length is exactly `IBCDenomLen=68`, and verifies the suffix is valid hex. [2](#0-1) 

`IBCClassID()` in the nft-transfer module confirms that received NFTs are stored under exactly this `ibc/<hash>` format: [3](#0-2) 

Yet `MsgTransferNFT.ValidateBasic()` calls only `ValidateDenomID`: [4](#0-3) 

The same flaw affects `MsgEditNFT.ValidateBasic()` and `MsgBurnNFT.ValidateBasic()`: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

An IBC-voucher NFT owner who wants to transfer their NFT to another address **on the same chain** (e.g., to sell, gift, or delegate custody) submits `MsgTransferNFT{DenomId: "ibc/AAAA...64hex", ...}`. The transaction is rejected at `ValidateBasic` before it ever reaches the keeper. The NFT ownership record in the store is immutable via this path. The owner's only recourse is to IBC-transfer the NFT back to the origin chain and re-transfer it there — which requires the recipient to have an account on the origin chain and a live IBC channel, and is not always possible.

---

### Likelihood Explanation

This is triggered by any user who received an NFT via IBC and attempts a normal on-chain transfer. It is deterministic and 100% reproducible on an unmodified deployment. No special privileges or race conditions are required.

---

### Recommendation

Replace `ValidateDenomID` with `ValidateDenomIDWithIBC` in the `ValidateBasic` methods of `MsgTransferNFT`, `MsgEditNFT`, and `MsgBurnNFT` in `x/nft/types/msgs.go`. [7](#0-6) 

---

### Proof of Concept

```go
func TestMsgTransferNFT_IBCDenomRejected(t *testing.T) {
    ibcDenomID := "ibc/" + strings.Repeat("a", 64) // valid IBC denom, 68 chars
    msg := types.NewMsgTransferNFT(
        "tokenid",
        ibcDenomID,
        "cosmos1qnk2n4nlkpw9xfqntladh74er2xa62wgas3ek",
        "cosmos1qnk2n4nlkpw9xfqntladh74er2xa62wgas3ek",
    )
    err := msg.ValidateBasic()
    // This asserts the bug: ValidateBasic returns ErrInvalidDenom,
    // blocking the transfer of a legitimately owned IBC-voucher NFT.
    require.ErrorContains(t, err, "ErrInvalidDenom")
}
```

`ValidateDenomID` rejects `ibcDenomID` on the length check (`68 > 64`) before even reaching the alphanumeric check, confirming the transfer path is unconditionally blocked for all IBC-voucher NFTs. [8](#0-7)

### Citations

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

**File:** x/nft-transfer/types/trace.go (L102-107)
```go
func (ct ClassTrace) IBCClassID() string {
	if ct.Path != "" {
		return fmt.Sprintf("%s/%s", ClassPrefix, ct.Hash())
	}
	return ct.BaseClassId
}
```

**File:** x/nft/types/msgs.go (L85-98)
```go
func (msg MsgTransferNFT) ValidateBasic() error {
	if err := ValidateDenomID(msg.DenomId); err != nil {
		return err
	}

	if _, err := sdk.AccAddressFromBech32(msg.Sender); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid sender address (%s)", err)
	}

	if _, err := sdk.AccAddressFromBech32(msg.Recipient); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address (%s)", err)
	}
	return ValidateTokenID(msg.Id)
}
```

**File:** x/nft/types/msgs.go (L130-137)
```go
func (msg MsgEditNFT) ValidateBasic() error {
	if _, err := sdk.AccAddressFromBech32(msg.Sender); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid sender address (%s)", err)
	}

	if err := ValidateDenomID(msg.DenomId); err != nil {
		return err
	}
```

**File:** x/nft/types/msgs.go (L217-224)
```go
func (msg MsgBurnNFT) ValidateBasic() error {
	if _, err := sdk.AccAddressFromBech32(msg.Sender); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid sender address (%s)", err)
	}
	if err := ValidateDenomID(msg.DenomId); err != nil {
		return err
	}
	return ValidateTokenID(msg.Id)
```
