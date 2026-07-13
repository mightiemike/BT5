The vulnerability is real and confirmed by the production code and its own test suite.

### Title
NFTs Become Permanently Unburnable After Transfer to Non-Creator — (`x/nft/keeper/keeper.go`)

### Summary

`BurnNFT` enforces a dual guard requiring the caller to be **both** the current NFT owner **and** the denom creator simultaneously. After any transfer to a non-creator address, no party can ever satisfy both conditions, making the NFT permanently unburnable and locking supply accounting.

### Finding Description

`keeper.BurnNFT` applies two sequential authorization checks: [1](#0-0) 

```go
// line 146-154
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
if err != nil {
    return err
}
_, err = k.IsDenomCreator(ctx, denomID, owner)
if err != nil {
    return err
}
```

`IsOwner` requires the caller to be the current NFT owner: [2](#0-1) 

`IsDenomCreator` requires the caller to be the original denom creator: [3](#0-2) 

The `MsgBurnNFT` message server routes directly to this keeper function with no additional bypass: [4](#0-3) 

The exploit sequence:
1. `IssueDenom(alice)` — alice is denom creator
2. `MintNFT(alice → bob)` — bob is now owner, alice is still creator
3. `BurnNFT(bob, denomID, tokenID)` — fails `IsDenomCreator` (bob is not creator)
4. `BurnNFT(alice, denomID, tokenID)` — fails `IsOwner` (alice is not owner)

The NFT is now permanently stuck. The only escape is for bob to transfer back to alice first, but this requires bob's cooperation and is not always possible (e.g., lost key, smart contract escrow, IBC escrow address).

The project's own test suite explicitly documents and accepts this behavior: [5](#0-4) 

The spec also codifies this as intentional design: [6](#0-5) 

### Impact Explanation

- Any NFT transferred to a non-creator address becomes permanently unburnable via `MsgBurnNFT`.
- `GetTotalSupply` will permanently over-count, breaking supply invariants.
- NFT owners lose the ability to destroy their own assets — a fundamental property of NFT ownership.
- If the NFT is escrowed (e.g., IBC transfer to a non-origin chain and back), the escrow address is not the denom creator, so `BurnNFTUnverified` is used for IBC but `BurnNFT` (the user-facing path) remains broken for all transferred NFTs.

### Likelihood Explanation

This is triggered by the most ordinary NFT workflow: mint to a recipient other than the creator. Any marketplace, airdrop, or gift scenario produces this state. The path is reachable via a standard signed `MsgBurnNFT` transaction with no special privileges required.

### Recommendation

Remove the `IsDenomCreator` check from `BurnNFT`. Burning should only require ownership. If the intent is to allow the creator to burn any NFT regardless of ownership (an admin burn), that should be a separate message (`MsgAdminBurnNFT`) with explicit semantics. The IBC path already demonstrates the correct pattern via `BurnNFTUnverified`, which only checks `IsOwner`: [7](#0-6) 

### Proof of Concept

The existing test at lines 172–184 of `keeper_test.go` is itself the proof of concept — it explicitly shows that after `TransferOwner(address → address2)`, calling `BurnNFT(address2)` returns `ErrUnauthorized` ("not the creator of denom"), and the workaround is to transfer back to the creator before burning: [8](#0-7) 

#Vulnerability confirmed.

### Citations

**File:** x/nft/keeper/keeper.go (L141-161)
```go
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

**File:** x/nft/keeper/nft.go (L45-56)
```go
func (k Keeper) IsOwner(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) (types.BaseNFT, error) {
	nft, err := k.GetNFT(ctx, denomID, tokenID)
	if err != nil {
		return types.BaseNFT{}, err
	}

	if !owner.Equals(nft.GetOwner()) {
		return types.BaseNFT{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the owner of %s/%s", owner.String(), denomID, tokenID)
	}

	return nft.(types.BaseNFT), nil
}
```

**File:** x/nft/keeper/denom.go (L81-97)
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

	return denom, nil
}
```

**File:** x/nft/keeper/msg_server.go (L166-175)
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
```

**File:** x/nft/keeper/keeper_test.go (L161-193)
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
}
```

**File:** x/nft/spec/02_messages.md (L111-112)
```markdown
This message type is used for burning non-fungible tokens which destroys and deletes them. `Sender` of this message
should be the `Owner` of the NFT and `Creator` of the denomination corresponding to `DenomId`.
```
