### Title
BurnNFT Requires Simultaneous IsOwner AND IsDenomCreator — NFT Becomes Permanently Unburnable After Transfer - (`x/nft/keeper/keeper.go`)

### Summary
`BurnNFT` enforces two sequential authorization checks — `IsOwner` **and** `IsDenomCreator` — on the same `owner` address. Once an NFT is transferred away from the denom creator, no single address can ever satisfy both conditions simultaneously, making the NFT permanently unburnable.

### Finding Description

The keeper-level `BurnNFT` function applies both checks unconditionally: [1](#0-0) 

```
BurnNFT(owner):
  1. IsOwner(owner)       → passes only if owner == current NFT owner
  2. IsDenomCreator(owner) → passes only if owner == denom creator
```

Both must pass for the **same** address. `IsDenomCreator` is: [2](#0-1) 

And `IsOwner` is: [3](#0-2) 

`TransferOwner` only requires `IsOwner` (no creator check), so ownership can freely move away from the creator: [4](#0-3) 

**Concrete four-step sequence (all valid production transactions):**

| Step | Msg | Result |
|---|---|---|
| 1 | `MsgIssueDenom` (sender=creator) | denom created, `denom.Creator = creator` |
| 2 | `MsgMintNFT` (sender=creator, recipient=victim) | NFT minted, `nft.Owner = victim` |
| 3 | `MsgTransferNFT` (sender=victim, recipient=attacker) | `nft.Owner = attacker` |
| 4a | `MsgBurnNFT` (sender=attacker) | **FAIL** — attacker is not `IsDenomCreator` |
| 4b | `MsgBurnNFT` (sender=creator) | **FAIL** — creator is not `IsOwner` |

After step 3, no address in existence satisfies both predicates simultaneously. The NFT is permanently unburnable.

### Impact Explanation

Any NFT that has ever been transferred away from its denom creator can never be burned by anyone. If burn semantics carry economic weight (e.g., burn-to-redeem, deflationary mechanics, IBC escrow release via `BurnNFTUnverified` is a separate path but the public `BurnNFT` msg is blocked), the NFT is permanently locked in an unburnable state. The supply counter can never be decremented for that token. [5](#0-4) 

### Likelihood Explanation

This is triggered by any normal transfer of an NFT away from the creator — an everyday operation. No attacker privilege is required; even an innocent transfer by the original recipient triggers the condition. The `TransferOwner` path has no restriction preventing this state.

### Recommendation

`BurnNFT` should require **either** the current owner **or** the denom creator, not both simultaneously. The check should be:

```go
_, ownerErr := k.IsOwner(ctx, denomID, tokenID, owner)
_, creatorErr := k.IsDenomCreator(ctx, denomID, owner)
if ownerErr != nil && creatorErr != nil {
    return sdkerrors.Wrapf(types.ErrUnauthorized, ...)
}
```

Or, more conservatively, restrict burn to the current owner only (matching standard NFT semantics), and separately allow the creator to reclaim via a dedicated admin operation.

### Proof of Concept

```go
// keeper integration test
creator  := sdk.AccAddress([]byte("creator"))
victim   := sdk.AccAddress([]byte("victim"))
attacker := sdk.AccAddress([]byte("attacker"))

k.IssueDenom(ctx, "denom1", "TestDenom", "", "", creator)
k.MintNFT(ctx, "denom1", "nft1", "", "", "", creator, victim)
k.TransferOwner(ctx, "denom1", "nft1", victim, attacker)

err1 := k.BurnNFT(ctx, "denom1", "nft1", attacker) // ErrUnauthorized: not creator
err2 := k.BurnNFT(ctx, "denom1", "nft1", creator)  // ErrUnauthorized: not owner
// Both non-nil → NFT is permanently unburnable
``` [6](#0-5)

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
