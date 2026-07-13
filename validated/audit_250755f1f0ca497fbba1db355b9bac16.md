### Title
BurnNFT Requires Simultaneous Owner + Creator — NFT Becomes Permanently Unburnable After Transfer - (`x/nft/keeper/keeper.go`)

### Summary

`BurnNFT` enforces two sequential guards: `IsOwner` **and** `IsDenomCreator`. After any `TransferOwner` call, these two properties are held by different addresses and can never be satisfied simultaneously by a single caller, making the NFT permanently unburnable.

### Finding Description

`BurnNFT` in `keeper.go` performs two independent authorization checks:

```go
// keeper.go lines 146–154
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
if err != nil {
    return err
}

_, err = k.IsDenomCreator(ctx, denomID, owner)
if err != nil {
    return err
}
``` [1](#0-0) 

`IsOwner` checks that `owner` equals `nft.Owner` in KV-store state. [2](#0-1) 

`IsDenomCreator` checks that `owner` equals `denom.Creator`, which is set once at `IssueDenom` and is **immutable** — there is no denom-creator transfer operation. [3](#0-2) 

`TransferOwner` (called by `MsgTransferNFT`) only requires `IsOwner` and freely reassigns `nft.Owner` to any destination address, with no creator restriction. [4](#0-3) 

**Concrete attack / accidental path:**

| Step | Action | State after |
|------|--------|-------------|
| 1 | Alice calls `MsgIssueDenom` | `denom.Creator = Alice` |
| 2 | Alice calls `MsgMintNFT` (recipient = Alice) | `nft.Owner = Alice` |
| 3 | Alice calls `MsgTransferNFT` (recipient = Bob) | `nft.Owner = Bob` |
| 4 | Bob calls `MsgBurnNFT` | Fails `IsDenomCreator` — Bob ≠ Alice |
| 5 | Alice calls `MsgBurnNFT` | Fails `IsOwner` — Alice ≠ Bob |

After step 3, **no address can ever satisfy both guards simultaneously** unless the creator happens to be the current owner. The NFT is permanently unburnable.

### Impact Explanation

The NFT is permanently locked in an unburnable state. It can still be transferred, but the destroy/burn lifecycle operation is irrecoverably broken for any NFT that has been transferred away from the denom creator. This violates the invariant that an NFT owner must be able to destroy their own asset, and it is irreversible — `denom.Creator` is immutable and there is no admin override path.

### Likelihood Explanation

This is triggered by any ordinary `MsgTransferNFT` from the creator to a third party — a completely routine, unprivileged on-chain action. No special privileges, governance, or key compromise are required. Any user who mints and then transfers an NFT will encounter this.

### Recommendation

`BurnNFT` should require **either** the current owner **or** the denom creator, not both simultaneously. The most natural fix is to require only `IsOwner` (matching how `TransferOwner` works), and optionally allow the denom creator to burn as a separate privileged path:

```go
// Option A: owner-only burn (symmetric with transfer)
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)

// Option B: owner OR creator
_, isOwnerErr := k.IsOwner(ctx, denomID, tokenID, owner)
_, isCreatorErr := k.IsDenomCreator(ctx, denomID, owner)
if isOwnerErr != nil && isCreatorErr != nil {
    return types.ErrUnauthorized
}
```

### Proof of Concept

```go
func TestBurnNFTAfterTransfer(t *testing.T) {
    ctx, keeper := setupKeeper(t)
    alice := sdk.AccAddress([]byte("alice"))
    bob   := sdk.AccAddress([]byte("bob"))

    // 1. Alice issues denom
    keeper.IssueDenom(ctx, "testdenom", "Test", "", "", alice)

    // 2. Alice mints NFT to herself
    keeper.MintNFT(ctx, "testdenom", "nft1", "", "", "", alice, alice)

    // 3. Alice transfers to Bob
    keeper.TransferOwner(ctx, "testdenom", "nft1", alice, bob)

    // 4. Bob cannot burn — not creator
    err := keeper.BurnNFT(ctx, "testdenom", "nft1", bob)
    require.ErrorContains(t, err, "ErrUnauthorized") // bob is not creator

    // 5. Alice cannot burn — not owner
    err = keeper.BurnNFT(ctx, "testdenom", "nft1", alice)
    require.ErrorContains(t, err, "ErrUnauthorized") // alice is not owner

    // NFT is permanently unburnable
}
```

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
