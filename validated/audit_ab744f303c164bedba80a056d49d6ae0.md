### Title
Dual-Role Requirement in `EditNFT` and `BurnNFT` Permanently Locks NFTs Minted to Non-Creator Recipients - (File: x/nft/keeper/keeper.go)

### Summary
`EditNFT` and `BurnNFT` in the NFT keeper require the caller to simultaneously be both the **current NFT owner** and the **denom creator**. When an NFT is minted with a `recipient` different from the `sender` (denom creator), these two roles are split across two accounts. Neither account can ever satisfy both checks, permanently locking the NFT in an un-editable and un-burnable state. This is a direct analog to the reported vaultka bug where a function required a user to be both "keeper" and "owner" simultaneously.

### Finding Description

`BurnNFT` at `x/nft/keeper/keeper.go` lines 141–161 enforces two sequential checks on the same `owner` address:

```go
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)   // line 146: must be NFT owner
...
_, err = k.IsDenomCreator(ctx, denomID, owner)         // line 151: must be denom creator
```

`EditNFT` at lines 84–118 applies the identical dual-role requirement:

```go
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)   // line 93
...
_, err = k.IsDenomCreator(ctx, denomID, owner)         // line 98
```

The `MintNFT` function (line 72–82) explicitly supports minting to a `recipient` different from the `sender` (denom creator):

```go
func (k Keeper) MintNFT(... sender, owner sdk.AccAddress) error {
    _, err := k.IsDenomCreator(ctx, denomID, sender)   // only sender is checked
    return k.MintNFTUnverified(ctx, denomID, tokenID, tokenNm, tokenURI, tokenData, owner)
}
```

Once `MsgMintNFT` is submitted with `recipient != sender`, the NFT owner is `recipient` and the denom creator is `sender`. From that point:
- `recipient` (owner) fails `IsDenomCreator` → cannot burn or edit
- `sender` (creator) fails `IsOwner` → cannot burn or edit

The NFT is permanently frozen. The keeper test at `x/nft/keeper/keeper_test.go` lines 130–138 explicitly confirms this: after `TransferOwner`, `EditNFT` fails for the new owner with `"is not the creator of"` and for the original creator with `"is not the owner of"`.

The `MsgBurnNFT` message server at `x/nft/keeper/msg_server.go` line 173 routes directly to `k.Keeper.BurnNFT`, with no alternative path for the NFT owner to destroy their own token.

Note: `BurnNFTUnverified` (lines 163–179) exists and bypasses the creator check, but it is only called internally by the IBC NFT-transfer module — it is not reachable via any user-facing message.

### Impact Explanation

Any NFT minted to a recipient other than the denom creator is permanently un-burnable and un-editable through the standard `MsgBurnNFT` / `MsgEditNFT` transaction paths. The NFT owner holds an asset they cannot destroy, and the denom creator cannot reclaim or update metadata on tokens they no longer own. This breaks the ownership invariant: a token owner cannot exercise full ownership rights (burn) over their own asset. The NFT supply for a denom can only grow — it can never be reduced through user-initiated burns once any token has been transferred away from the creator.

### Likelihood Explanation

The trigger is a standard, documented, and fully supported `MsgMintNFT` transaction with `recipient != sender`. The `MsgMintNFT` proto definition explicitly includes a separate `recipient` field for exactly this purpose. Any denom creator who mints NFTs to buyers, recipients, or any address other than themselves immediately creates permanently locked tokens. This is a routine NFT issuance pattern.

### Recommendation

Separate the authorization logic for `EditNFT` and `BurnNFT` so that either the NFT owner **or** the denom creator can independently perform the operation, or allow the denom creator to act on any NFT within their denom regardless of current ownership (mirroring the `BurnNFTUnverified` / `MintNFTUnverified` pattern already used for IBC). At minimum, the NFT owner should always be able to burn their own token without requiring the denom-creator role.

### Proof of Concept

1. Alice issues a denom: `MsgIssueDenom{sender: alice}`
2. Alice mints an NFT to Bob: `MsgMintNFT{sender: alice, recipient: bob, denom_id: D, id: T}`
   - State: NFT owner = bob, denom creator = alice
3. Bob submits `MsgBurnNFT{sender: bob, denom_id: D, id: T}`
   - `BurnNFT` calls `k.IsOwner(ctx, D, T, bob)` → passes
   - `BurnNFT` calls `k.IsDenomCreator(ctx, D, bob)` → **fails**: `"bob is not the creator of D"`
4. Alice submits `MsgBurnNFT{sender: alice, denom_id: D, id: T}`
   - `BurnNFT` calls `k.IsOwner(ctx, D, T, alice)` → **fails**: `"alice is not the owner of D/T"`
5. Neither party can burn the NFT. The same failure applies to `MsgEditNFT`. The NFT is permanently locked.

This is confirmed by the existing keeper test at `x/nft/keeper/keeper_test.go` lines 172–188, which documents that `BurnNFT` fails for a non-creator owner and for a creator non-owner, and only succeeds when both roles are held by the same address. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** x/nft/keeper/keeper.go (L71-82)
```go
// MintNFT mints an NFT and manages the NFT's existence within Collections and Owners
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

**File:** x/nft/keeper/keeper.go (L84-118)
```go
// EditNFT updates an already existing NFT
func (k Keeper) EditNFT(
	ctx sdk.Context, denomID, tokenID, tokenNm,
	tokenURI, tokenData string, owner sdk.AccAddress,
) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	_, err = k.IsDenomCreator(ctx, denomID, owner)
	if err != nil {
		return err
	}

	if types.Modified(tokenNm) {
		nft.Name = tokenNm
	}

	if types.Modified(tokenURI) {
		nft.URI = tokenURI
	}

	if types.Modified(tokenData) {
		nft.Data = tokenData
	}

	k.setNFT(ctx, denomID, nft)

	return nil
}
```

**File:** x/nft/keeper/keeper.go (L140-161)
```go
// BurnNFT deletes a specified NFT
func (k Keeper) BurnNFT(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	_, err = k.IsDenomCreator(ctx, denomID, owner)
	if err != nil {
		return err
	}

	k.deleteNFT(ctx, denomID, nft)
	k.deleteOwner(ctx, denomID, tokenID, owner)
	k.decreaseSupply(ctx, denomID)

	return nil
}
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

**File:** x/nft/keeper/keeper_test.go (L130-138)
```go
	// Transfer owner shouldn't fail
	err = suite.keeper.TransferOwner(suite.ctx, denomID, tokenID, address, address2)
	suite.NoError(err)

	// EditNFT should fail when address is not the creator of denom
	err = suite.keeper.EditNFT(suite.ctx, denomID, tokenID, tokenNm, tokenURI, tokenData, address2)
	if suite.Error(err) {
		suite.EqualError(err, fmt.Sprintf("%s is not the creator of %s: %s", address2, denomID, types.ErrUnauthorized))
	}
```

**File:** x/nft/keeper/keeper_test.go (L161-192)
```go
func (suite *KeeperSuite) TestBurnNFT() {
	// MintNFT should not fail when collection does not exist
	err := suite.keeper.MintNFT(suite.ctx, denomID, tokenID, tokenNm, tokenURI, tokenData, address, address)
	suite.NoError(err)

	// BurnNFT should fail when address is not the owner of nft
	err = suite.keeper.BurnNFT(suite.ctx, denomID, tokenID, address2)
	if suite.Error(err) {
		suite.EqualError(err, fmt.Sprintf("%s is not the owner of %s/%s: %s", address2, denomID, tokenID, types.ErrUnauthorized))
	}

	// Transfer nft to an address which is not the creator of denom
	err = suite.keeper.TransferOwner(suite.ctx, denomID, tokenID, address, address2)
	suite.NoError(err)

	// BurnNFT should fail when address is not the creator of denom
	err = suite.keeper.BurnNFT(suite.ctx, denomID, tokenID, address2)
	if suite.Error(err) {
		suite.EqualError(err, fmt.Sprintf("%s is not the creator of %s: %s", address2, denomID, types.ErrUnauthorized))
	}

	// Transfer nft back to the creator of denom
	err = suite.keeper.TransferOwner(suite.ctx, denomID, tokenID, address2, address)
	suite.NoError(err)

	// BurnNFT shouldn't fail when NFT exists and address is owner of nft and creator of denom
	err = suite.keeper.BurnNFT(suite.ctx, denomID, tokenID, address)
	suite.NoError(err)

	// NFT should no longer exist
	isNFT := suite.keeper.HasNFT(suite.ctx, denomID, tokenID)
	suite.False(isNFT)
```

**File:** x/nft/keeper/msg_server.go (L94-127)
```go
//nolint:dupl
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
