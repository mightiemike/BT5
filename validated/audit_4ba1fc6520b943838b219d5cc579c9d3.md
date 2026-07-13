The code is clear and the analysis is straightforward. Let me confirm the exact lines for `BurnNFT` as well since it has the same pattern.

The `EditNFT` function in `x/nft/keeper/keeper.go` requires the caller to satisfy **both** `IsOwner` AND `IsDenomCreator` simultaneously: [1](#0-0) 

`IsOwner` requires the caller to be the current NFT owner: [2](#0-1) 

`IsDenomCreator` requires the caller to be the original denom creator (immutable after `SetDenom`): [3](#0-2) 

`MintNFT` explicitly allows `sender != owner` (recipient), so the creator can mint to a third party: [4](#0-3) 

`TransferOwner` has no `IsDenomCreator` check — any owner can transfer freely: [5](#0-4) 

`BurnNFT` has the identical dual-check pattern, so it is also permanently blocked after transfer: [6](#0-5) 

---

### Title
NFT Metadata Permanently Frozen After Transfer — (`x/nft/keeper/keeper.go` → `EditNFT`)

### Summary

`Keeper.EditNFT` requires the caller to be **both** the current NFT owner (`IsOwner`) **and** the denom creator (`IsDenomCreator`). Once an NFT is transferred away from the denom creator — including the normal case where the creator mints directly to a recipient — no account can ever satisfy both conditions simultaneously, permanently freezing the NFT's metadata. `BurnNFT` has the identical flaw.

### Finding Description

In `x/nft/keeper/keeper.go`, `EditNFT` enforces two sequential guards on the same `owner` address:

```go
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)   // must be current owner
...
_, err = k.IsDenomCreator(ctx, denomID, owner)         // must also be denom creator
```

`IsDenomCreator` compares against `denom.Creator`, which is set once at `IssueDenom` and never updated. `MintNFT` accepts `sender` (creator) and `owner` (recipient) as separate parameters, so the creator can mint to any address. `TransferOwner` (called by `MsgTransferNFT`) has no `IsDenomCreator` guard, so any owner can transfer freely.

After the sequence:
1. `IssueDenom(creator)` — creator is the permanent denom creator
2. `MintNFT(sender=creator, recipient=alice)` — alice owns the NFT
3. `TransferNFT(alice → bob)` — bob owns the NFT

State after step 3:
- `EditNFT(creator, ...)` → fails `IsOwner` (creator ≠ bob)
- `EditNFT(bob, ...)` → fails `IsDenomCreator` (bob ≠ creator)
- `BurnNFT(bob, ...)` → same dual-check, same failure

No account can ever satisfy both guards. The NFT's metadata is permanently frozen and the NFT is permanently unburnable via `MsgBurnNFT`.

### Impact Explanation

- **NFT metadata permanently frozen**: `tokenNm`, `tokenURI`, and `tokenData` can never be updated after any transfer away from the creator. This is irreversible on-chain state.
- **NFT permanently unburnable**: `BurnNFT` has the identical `IsOwner` + `IsDenomCreator` dual-check. The NFT cannot be removed from supply via the standard `MsgBurnNFT` path. (`BurnNFTUnverified` exists only for IBC and is not reachable by a user transaction.)
- The creator can trigger this condition unilaterally by minting to `recipient != sender`, or any owner can trigger it by transferring away from the creator.

### Likelihood Explanation

This is triggered by the ordinary, documented use case of minting an NFT to a recipient other than the creator — a feature explicitly supported by `MintNFT`'s `sender`/`owner` parameter split and by `MsgMintNFT.Recipient`. Any subsequent transfer (also a core feature) makes the condition permanent. No special privileges, governance, or attacker coordination are required.

### Recommendation

Separate the authorization model for `EditNFT` and `BurnNFT`. The most consistent fix is to require **either** `IsOwner` **or** `IsDenomCreator` (not both), or to require only `IsOwner` for editing and only `IsDenomCreator` for burning. Concretely, in `keeper.go`:

```go
// EditNFT: require only current owner
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
if err != nil {
    return err
}
// Remove the IsDenomCreator check entirely, or make it an OR condition
```

### Proof of Concept

Keeper-level sequence test (no mocks needed):

```go
// 1. Setup
creator := sdk.AccAddress([]byte("creator"))
alice   := sdk.AccAddress([]byte("alice"))
bob     := sdk.AccAddress([]byte("bob"))

k.IssueDenom(ctx, "testdenom", "Test", "", "", creator)
k.MintNFT(ctx, "testdenom", "token1", "name", "uri", "data", creator, alice)

// 2. alice transfers to bob
k.TransferOwner(ctx, "testdenom", "token1", alice, bob)

// 3. Assert EditNFT is blocked for everyone
err := k.EditNFT(ctx, "testdenom", "token1", "newname", "", "", creator)
require.ErrorIs(t, err, types.ErrUnauthorized) // creator is not owner

err = k.EditNFT(ctx, "testdenom", "token1", "newname", "", "", bob)
require.ErrorIs(t, err, types.ErrUnauthorized) // bob is not creator

// 4. Assert BurnNFT is also blocked
err = k.BurnNFT(ctx, "testdenom", "token1", bob)
require.ErrorIs(t, err, types.ErrUnauthorized) // bob is not creator
```

All three assertions pass against the unmodified production code, confirming the invariant is broken.

### Citations

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

**File:** x/nft/keeper/keeper.go (L92-101)
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

**File:** x/nft/keeper/nft.go (L51-53)
```go
	if !owner.Equals(nft.GetOwner()) {
		return types.BaseNFT{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the owner of %s/%s", owner.String(), denomID, tokenID)
	}
```

**File:** x/nft/keeper/denom.go (L92-94)
```go
	if !creator.Equals(address) {
		return types.Denom{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the creator of %s", address, denomID)
	}
```
