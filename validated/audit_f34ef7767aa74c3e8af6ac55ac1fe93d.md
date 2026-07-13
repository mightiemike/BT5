The vulnerability hypothesis is **confirmed**. The `BurnNFT` keeper function imposes a conjunctive (`AND`) authorization check requiring the caller to simultaneously satisfy both `IsOwner` and `IsDenomCreator`. After a transfer, no single address can satisfy both conditions, making the NFT permanently unburnable.

---

### Title
Transferred NFTs Are Permanently Unburnable Due to Conjunctive `IsOwner && IsDenomCreator` Guard in `BurnNFT` — (`x/nft/keeper/keeper.go`)

### Summary

`keeper.BurnNFT` requires the caller to be **both** the current NFT owner **and** the denom creator simultaneously. Once the denom creator transfers an NFT to any other address, neither party can ever satisfy both conditions at once, permanently locking the NFT.

### Finding Description

`BurnNFT` in `x/nft/keeper/keeper.go` performs two sequential authorization checks:

```go
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
if err != nil {
    return err
}

_, err = k.IsDenomCreator(ctx, denomID, owner)
if err != nil {
    return err
}
``` [1](#0-0) 

Both checks must pass for the same `owner` address. `IsOwner` verifies the caller holds the NFT, and `IsDenomCreator` verifies the caller created the denom. [2](#0-1) [3](#0-2) 

After `TransferOwner` moves the NFT from Alice (denom creator) to Bob:

- **Alice** fails `IsOwner` — she is no longer the owner.
- **Bob** fails `IsDenomCreator` — he did not create the denom. [4](#0-3) 

The NFT is now permanently unburnable through the `MsgBurnNFT` path. The only escape is Bob voluntarily transferring back to Alice, which is not guaranteed.

The existence of `BurnNFTUnverified` — which only checks `IsOwner` and is used for IBC — confirms the codebase itself recognizes that ownership alone is sufficient authorization for destruction in some contexts, making the dual-check in `BurnNFT` an inconsistency. [5](#0-4) 

### Impact Explanation

Any NFT minted by a denom creator and then transferred to another address becomes permanently unburnable. The NFT is locked in the recipient's account with no on-chain mechanism to destroy it, constituting a permanent lock of an economically valuable NFT. This is reachable via the standard `MsgMintNFT` → `MsgTransferNFT` → `MsgBurnNFT` transaction sequence with no special privileges required.

### Likelihood Explanation

This is the normal NFT lifecycle: mint, gift/sell (transfer), recipient burns. Any user who receives an NFT from a denom creator and attempts to burn it will be permanently blocked. The scenario is trivially reachable with two unprivileged on-chain transactions.

### Recommendation

`BurnNFT` should require **either** `IsOwner` **or** `IsDenomCreator`, not both. The owner of an NFT must always be able to destroy it. The correct guard is:

```go
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
if err != nil {
    return err
}
// IsDenomCreator check removed — ownership alone authorizes burn
```

If the intent is to allow the denom creator to burn NFTs they no longer own (a separate privilege), that should be an explicit OR branch, not a conjunctive requirement.

### Proof of Concept

Keeper-level test (no mocks needed):

1. Alice calls `IssueDenom` → denom `d1` created with `Creator = Alice`.
2. Alice calls `MintNFT(d1, t1, ..., sender=Alice, recipient=Alice)` → NFT owned by Alice.
3. Alice calls `TransferOwner(d1, t1, Alice, Bob)` → NFT now owned by Bob.
4. Assert `BurnNFT(d1, t1, Alice)` returns `ErrUnauthorized` (Alice fails `IsOwner`). ✓
5. Assert `BurnNFT(d1, t1, Bob)` returns `ErrUnauthorized` (Bob fails `IsDenomCreator`). ✓
6. NFT remains in state indefinitely — permanently locked. [6](#0-5)

### Citations

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

**File:** x/nft/keeper/keeper.go (L163-180)
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
}
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

**File:** x/nft/keeper/denom.go (L81-96)
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
```
