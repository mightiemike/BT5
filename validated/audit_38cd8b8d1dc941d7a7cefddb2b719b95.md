### Title
Missing Zero-Address Validation in `MsgMintNFT` and `MsgTransferNFT` Allows Accidental NFT Burns — (File: `x/nft/types/msgs.go`)

---

### Summary

`MsgMintNFT.ValidateBasic()` and `MsgTransferNFT.ValidateBasic()` in `x/nft/types/msgs.go` validate that the `Recipient` field is a syntactically valid bech32 address via `sdk.AccAddressFromBech32`, but neither function checks that the decoded address is not the zero address (20 zero bytes). A denom creator or NFT owner can therefore mint or transfer an NFT to the Cosmos zero address (`cosmos1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqnrql8a`), permanently and irrecoverably destroying it.

---

### Finding Description

In `x/nft/types/msgs.go`, both `MsgMintNFT.ValidateBasic()` and `MsgTransferNFT.ValidateBasic()` call `sdk.AccAddressFromBech32` on the recipient field and return an error only if the string is not a valid bech32 encoding:

```go
// MsgMintNFT
if _, err := sdk.AccAddressFromBech32(msg.Recipient); err != nil {
    return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid receipt address (%s)", err)
}
``` [1](#0-0) 

```go
// MsgTransferNFT
if _, err := sdk.AccAddressFromBech32(msg.Recipient); err != nil {
    return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address (%s)", err)
}
``` [2](#0-1) 

The Cosmos SDK zero address (20 zero bytes) is a valid bech32 string and passes `AccAddressFromBech32` without error. No subsequent check rejects it. The `msgServer.MintNFT` and `msgServer.TransferNFT` handlers in `x/nft/keeper/msg_server.go` also call `AccAddressFromBech32` on the recipient and pass the result directly to the keeper without any zero-address guard:

```go
recipient, err := sdk.AccAddressFromBech32(msg.Recipient)
if err != nil {
    return nil, err
}
// ... passed directly to k.Keeper.MintNFT / k.TransferOwner
``` [3](#0-2) [4](#0-3) 

The keeper-level `MintNFT` and `TransferOwner` functions also contain no zero-address check: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

An NFT minted or transferred to the zero address is permanently unrecoverable. No private key controls the zero address, so the NFT is effectively burned. The NFT's on-chain supply counter is not decremented (unlike `BurnNFT`), so the supply accounting becomes inconsistent: the token exists in state but is permanently inaccessible. Any royalty, utility, or financial value attached to the NFT is destroyed.

---

### Likelihood Explanation

The entry path is a standard, unprivileged Cosmos SDK transaction:

- **`MsgMintNFT`**: Any denom creator submitting a mint transaction can accidentally supply the zero address as `Recipient`. This is a realistic fat-finger error, especially when constructing transactions programmatically or via CLI with a blank/default address variable.
- **`MsgTransferNFT`**: Any NFT owner can accidentally transfer to the zero address. The CLI path (`x/nft/client/cli/tx.go`) accepts the recipient as a raw string argument with no additional guard.

No privileged role, leaked key, or social engineering is required. The transaction is accepted and finalized by the chain.

---

### Recommendation

Add an explicit zero-address check in both `ValidateBasic` implementations in `x/nft/types/msgs.go`:

```go
recipient, err := sdk.AccAddressFromBech32(msg.Recipient)
if err != nil {
    return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address (%s)", err)
}
if recipient.Empty() {
    return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "recipient address cannot be zero address")
}
```

Apply the same guard in `MsgMintNFT.ValidateBasic()` for the `Recipient` field. [7](#0-6) [8](#0-7) 

---

### Proof of Concept

1. A denom creator issues a denom via `MsgIssueDenom`.
2. The creator submits `MsgMintNFT` with `Recipient = "cosmos1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqnrql8a"` (the bech32 encoding of 20 zero bytes).
3. `MsgMintNFT.ValidateBasic()` calls `sdk.AccAddressFromBech32("cosmos1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqnrql8a")` — this succeeds and returns a 20-zero-byte `AccAddress`.
4. No further check rejects it. The transaction is broadcast and included in a block.
5. `msgServer.MintNFT` passes the zero `AccAddress` to `k.Keeper.MintNFT`, which calls `k.setNFT` and `k.setOwner` with the zero address as owner.
6. The NFT is now owned by the zero address. No account can sign a transaction to transfer or burn it. The NFT is permanently lost while still counted in the denom's supply.

The same flow applies to `MsgTransferNFT` with any existing NFT owner supplying the zero address as `Recipient`. [9](#0-8)

### Citations

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

**File:** x/nft/types/msgs.go (L176-190)
```go
func (msg MsgMintNFT) ValidateBasic() error {
	if _, err := sdk.AccAddressFromBech32(msg.Sender); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid sender address (%s)", err)
	}
	if _, err := sdk.AccAddressFromBech32(msg.Recipient); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid receipt address (%s)", err)
	}
	if err := ValidateDenomID(msg.DenomId); err != nil {
		return err
	}
	if err := ValidateTokenURI(msg.URI); err != nil {
		return err
	}
	return ValidateTokenID(msg.Id)
}
```

**File:** x/nft/keeper/msg_server.go (L55-72)
```go
	recipient, err := sdk.AccAddressFromBech32(msg.Recipient)
	if err != nil {
		return nil, err
	}

	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.MintNFT(ctx, msg.DenomId, msg.Id,
		msg.Name,
		msg.URI,
		msg.Data,
		sender,
		recipient,
	); err != nil {
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

**File:** x/nft/keeper/keeper.go (L55-68)
```go
	k.setNFT(
		ctx, denomID,
		types.NewBaseNFT(
			tokenID,
			tokenNm,
			owner,
			tokenURI,
			tokenData,
		),
	)
	k.setOwner(ctx, denomID, tokenID, owner)
	k.increaseSupply(ctx, denomID)

	return nil
```

**File:** x/nft/keeper/keeper.go (L72-82)
```go
func (k Keeper) MintNFT(
	ctx sdk.Context, denomID, tokenID, tokenNm,
	tokenURI, tokenData string, sender, owner sdk.AccAddress,
) error {
	_, err := k.IsDenomCreator(ctx, denomID, sender)
	if err != nil {
		return err
	}

	return k.MintNFTUnverified(ctx, denomID, tokenID, tokenNm, tokenURI, tokenData, owner)
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
