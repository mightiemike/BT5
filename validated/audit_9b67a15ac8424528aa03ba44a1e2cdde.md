### Title
NFTs Become Permanently Uneditable and Unburnable After Any Transfer — (`x/nft/keeper/keeper.go`)

### Summary

`EditNFT` and `BurnNFT` both require the caller to simultaneously satisfy `IsOwner` **and** `IsDenomCreator` on the same address. `TransferOwner` has no such dual requirement and freely moves ownership. After any transfer, ownership and denom-creator status are split across two different addresses, making it impossible for any party to call `EditNFT` or `BurnNFT` successfully.

### Finding Description

`EditNFT` enforces two sequential guards on the single `owner` parameter: [1](#0-0) 

`BurnNFT` applies the identical dual guard: [2](#0-1) 

`TransferOwner`, however, only checks `IsOwner` for the sender and performs no `IsDenomCreator` check before updating ownership: [3](#0-2) 

`IsDenomCreator` compares the caller against the immutable `denom.Creator` field stored at `IssueDenom` time and never updated: [4](#0-3) 

The concrete post-transfer state:

| Caller | `IsOwner` | `IsDenomCreator` | Result |
|---|---|---|---|
| New owner (B) | ✅ pass | ❌ fail | `ErrUnauthorized` |
| Original creator (A) | ❌ fail | ✅ pass | `ErrUnauthorized` |

Both `EditNFT` and `BurnNFT` return `ErrUnauthorized` for every possible caller. The NFT is permanently frozen.

The full reachable transaction path through `MsgServer`:

- `MsgIssueDenom` → `msgServer.IssueDenom` → `Keeper.IssueDenom`
- `MsgMintNFT` → `msgServer.MintNFT` → `Keeper.MintNFT`
- `MsgTransferNFT` → `msgServer.TransferNFT` → `Keeper.TransferOwner`
- `MsgEditNFT` → `msgServer.EditNFT` → `Keeper.EditNFT` ← **permanently blocked**
- `MsgBurnNFT` → `msgServer.BurnNFT` → `Keeper.BurnNFT` ← **permanently blocked** [5](#0-4) [6](#0-5) 

There is no admin override, governance escape hatch, or alternative burn path for non-IBC NFTs. `BurnNFTUnverified` exists but is only called from the IBC transfer module, not from any user-facing `MsgServer` handler. [7](#0-6) 

### Impact Explanation

Any NFT that has ever been transferred is permanently uneditable and unburnable by any party. The NFT supply counter for the denom can never decrease for those tokens. Metadata is frozen. The only recovery is if the new owner voluntarily transfers back to the original creator — which is not guaranteed and cannot be enforced on-chain. This breaks the core ownership invariant of the NFT module.

### Likelihood Explanation

The trigger is a single `MsgTransferNFT` transaction, which is a standard, fully supported production message. Any denom creator who transfers an NFT — including to themselves via a different key, or to any third party — immediately triggers the lockup. No special privileges, governance, or operator access are required.

### Recommendation

Remove the `IsDenomCreator` check from both `EditNFT` and `BurnNFT`. Ownership alone should be sufficient authorization for editing and burning. The `IsDenomCreator` check is appropriate for `MintNFT` (controlling who can mint into a denom) but is incorrect for operations that act on a specific token already owned by the caller.

Alternatively, if the design intent is that only the creator can edit/burn, then `TransferOwner` should be blocked or the denom creator field should be updatable — but this would be a more invasive design change.

### Proof of Concept

```go
// keeper_test.go (pseudocode for a standard Go keeper unit test)
func TestNFTLockedAfterTransfer(t *testing.T) {
    ctx, keeper := setupKeeper(t)
    creator := sdk.AccAddress([]byte("creator"))
    victim  := sdk.AccAddress([]byte("victim"))

    // 1. Issue denom
    err := keeper.IssueDenom(ctx, "testdenom", "Test", "", "", creator)
    require.NoError(t, err)

    // 2. Mint NFT to creator
    err = keeper.MintNFT(ctx, "testdenom", "token1", "MyNFT", "", "", creator, creator)
    require.NoError(t, err)

    // 3. Transfer to victim
    err = keeper.TransferOwner(ctx, "testdenom", "token1", creator, victim)
    require.NoError(t, err)

    // 4. Victim cannot edit (IsOwner=pass, IsDenomCreator=fail)
    err = keeper.EditNFT(ctx, "testdenom", "token1", "new", "", "", victim)
    require.ErrorContains(t, err, "unauthorized") // ← confirmed

    // 5. Creator cannot edit (IsOwner=fail, IsDenomCreator=pass)
    err = keeper.EditNFT(ctx, "testdenom", "token1", "new", "", "", creator)
    require.ErrorContains(t, err, "unauthorized") // ← confirmed

    // 6. Same for BurnNFT — both callers blocked
    err = keeper.BurnNFT(ctx, "testdenom", "token1", victim)
    require.ErrorContains(t, err, "unauthorized")
    err = keeper.BurnNFT(ctx, "testdenom", "token1", creator)
    require.ErrorContains(t, err, "unauthorized")
}
```

### Citations

**File:** x/nft/keeper/keeper.go (L93-101)
```go
	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	_, err = k.IsDenomCreator(ctx, denomID, owner)
	if err != nil {
		return err
	}
```

**File:** x/nft/keeper/keeper.go (L121-137)
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
```

**File:** x/nft/keeper/keeper.go (L146-153)
```go
	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	_, err = k.IsDenomCreator(ctx, denomID, owner)
	if err != nil {
		return err
```

**File:** x/nft/keeper/keeper.go (L163-179)
```go
// BurnNFTUnverified deletes a specified NFT without verifying if the owner is the creator of denom
// Needed for IBC transfer of NFT
func (k Keeper) BurnNFTUnverified(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	k.deleteNFT(ctx, denomID, nft)
	k.deleteOwner(ctx, denomID, tokenID, owner)
	k.decreaseSupply(ctx, denomID)

	return nil
```

**File:** x/nft/keeper/denom.go (L81-94)
```go
func (k Keeper) IsDenomCreator(ctx sdk.Context, denomID string, address sdk.AccAddress) (types.Denom, error) {
	denom, err := k.GetDenom(ctx, denomID)
	if err != nil {
		return types.Denom{}, err
	}

	creator, err := sdk.AccAddressFromBech32(denom.Creator)
	if err != nil {
		panic(err)
	}

	if !creator.Equals(address) {
		return types.Denom{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the creator of %s", address, denomID)
	}
```

**File:** x/nft/keeper/msg_server.go (L95-127)
```go
func (m msgServer) EditNFT(goCtx context.Context, msg *types.MsgEditNFT) (*types.MsgEditNFTResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.EditNFT(ctx, msg.DenomId, msg.Id,
		msg.Name,
		msg.URI,
		msg.Data,
		sender,
	); err != nil {
		return nil, err
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeEditNFT,
			sdk.NewAttribute(types.AttributeKeyTokenID, msg.Id),
			sdk.NewAttribute(types.AttributeKeyDenomID, msg.DenomId),
			sdk.NewAttribute(types.AttributeKeyTokenURI, msg.URI),
			sdk.NewAttribute(types.AttributeKeyOwner, msg.Sender),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.AttributeValueCategory),
			sdk.NewAttribute(sdk.AttributeKeySender, msg.Sender),
		),
	})

	return &types.MsgEditNFTResponse{}, nil
}
```

**File:** x/nft/keeper/msg_server.go (L166-191)
```go
func (m msgServer) BurnNFT(goCtx context.Context, msg *types.MsgBurnNFT) (*types.MsgBurnNFTResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.BurnNFT(ctx, msg.DenomId, msg.Id, sender); err != nil {
		return nil, err
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeBurnNFT,
			sdk.NewAttribute(types.AttributeKeyDenomID, msg.DenomId),
			sdk.NewAttribute(types.AttributeKeyTokenID, msg.Id),
			sdk.NewAttribute(types.AttributeKeyOwner, msg.Sender),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.AttributeValueCategory),
			sdk.NewAttribute(sdk.AttributeKeySender, msg.Sender),
		),
	})

	return &types.MsgBurnNFTResponse{}, nil
```
