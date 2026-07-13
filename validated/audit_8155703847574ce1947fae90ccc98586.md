The code confirms this is a real vulnerability. The evidence is unambiguous.

### Title
Non-Creator NFT Owner Permanently Blocked from Burning or Editing Their Own NFT — (`x/nft/keeper/keeper.go`)

### Summary

`BurnNFT` and `EditNFT` both require the caller to pass **two independent checks**: `IsOwner` AND `IsDenomCreator`. Since `TransferOwner` only requires `IsOwner`, any NFT transferred to a non-creator address becomes permanently un-burnable and un-editable by its new owner. The supply counter is never decremented, creating unbacked supply inflation.

### Finding Description

`BurnNFT` enforces a dual authorization check: [1](#0-0) 

Line 146 checks `IsOwner`, and line 151 checks `IsDenomCreator`. Both must pass. `EditNFT` has the identical pattern: [2](#0-1) 

However, `TransferOwner` only checks `IsOwner`: [3](#0-2) 

This means any address — including non-creators — can receive an NFT via `MsgTransferNFT`, but once they hold it, they cannot burn or edit it. The existing test suite **explicitly confirms** this behavior: [4](#0-3) 

The test at line 176 documents: "BurnNFT should fail when address is not the creator of denom" — after a transfer to `address2`, `BurnNFT(address2)` returns `ErrUnauthorized`. The NFT can only be burned after transferring it back to the creator (lines 182–188).

### Impact Explanation

- An NFT transferred to any non-creator address is **permanently locked**: the owner cannot burn or edit it.
- `decreaseSupply` is never called for such NFTs, so `GetTotalSupply(denomID)` remains inflated indefinitely.
- The only escape is for the non-creator to transfer the NFT back to the denom creator — but the creator has no obligation to burn it, and the non-creator has no recourse.
- `BurnNFTUnverified` (used by IBC) skips the creator check, confirming the dual-check in `BurnNFT` is not a universal design requirement but an inconsistency. [5](#0-4) 

### Likelihood Explanation

This is reachable via standard `MsgTransferNFT` → `MsgBurnNFT` transaction sequence. No privileged access, governance, or key compromise is required. Any user who receives an NFT from a creator triggers this condition.

### Recommendation

Remove the `IsDenomCreator` check from `BurnNFT` and `EditNFT`. The `IsOwner` check is sufficient authorization for an NFT owner to manage their own token. If the design intent is to restrict editing/burning to the creator, then `TransferOwner` should be blocked or the documentation must clearly state that transferred NFTs become permanently immutable — which is a severe UX and economic invariant violation.

### Proof of Concept

```
1. IssueDenom(creator, denomID)
2. MintNFT(creator, tokenID, recipient=alice)
3. TransferOwner(alice, bob, tokenID)          // succeeds — only IsOwner checked
4. BurnNFT(bob, tokenID)                       // FAILS: ErrUnauthorized "bob is not the creator of denomID"
5. GetTotalSupply(denomID) == 1                // supply never decremented; no valid burner exists
```

The keeper test at `x/nft/keeper/keeper_test.go` lines 172–180 is a ready-made reproduction of steps 3–4. [6](#0-5)

### Citations

**File:** x/nft/keeper/keeper.go (L85-101)
```go
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
