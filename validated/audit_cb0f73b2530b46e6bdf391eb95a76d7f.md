The `EditNFT` keeper function at lines 85–118 of `x/nft/keeper/keeper.go` contains a concrete AND-logic bug. Both `IsOwner` and `IsDenomCreator` are called sequentially on the **same address**, meaning the caller must simultaneously be the NFT owner **and** the denom creator. Once an NFT is minted to a different address (Bob), neither Bob nor Alice can ever satisfy both checks.

---

### Title
`EditNFT` Requires Caller to Be Both NFT Owner AND Denom Creator Simultaneously, Permanently Freezing NFT Metadata — (`x/nft/keeper/keeper.go`)

### Summary
`keeper.EditNFT` applies `IsOwner` and `IsDenomCreator` as a sequential AND gate on the same `owner` address. After `MintNFT` transfers ownership to Bob, no single address can satisfy both checks, making NFT metadata permanently uneditable.

### Finding Description

In `keeper.EditNFT`:

```go
// x/nft/keeper/keeper.go lines 93–101
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
if err != nil {
    return err
}

_, err = k.IsDenomCreator(ctx, denomID, owner)
if err != nil {
    return err
}
``` [1](#0-0) 

`IsOwner` returns `ErrUnauthorized` if `owner != nft.Owner`, and `IsDenomCreator` returns `ErrUnauthorized` if `owner != denom.Creator`. [2](#0-1) [3](#0-2) 

After Alice (denom creator) mints an NFT to Bob via `MintNFT`:

- **Bob calls `EditNFT`**: `IsOwner` passes, `IsDenomCreator` fails → `ErrUnauthorized`
- **Alice calls `EditNFT`**: `IsOwner` fails (Bob is owner) → `ErrUnauthorized`

The intended invariant — "the NFT owner **or** the denom creator may update metadata" — is violated by the AND logic. The only address that could ever succeed is one that is simultaneously the current NFT owner and the denom creator, which is impossible once the NFT has been transferred or minted to a third party.

The same AND-gate bug exists in `BurnNFT` (lines 146–154), meaning Bob also cannot burn his own NFT. [4](#0-3) 

### Impact Explanation
Once an NFT is minted to any address other than the denom creator, its metadata is permanently frozen — no on-chain transaction can update it. This permanently degrades the economic value of the NFT (e.g., dynamic art, game items, certificates whose metadata must evolve). The same logic also permanently locks the NFT against burning by its owner.

### Likelihood Explanation
This is triggered by the standard, intended workflow: issue denom → mint to a recipient who is not the creator. This is the primary use case for the NFT module. Every such NFT is affected unconditionally.

### Recommendation
Replace the AND gate with an OR gate:

```go
_, errOwner := k.IsOwner(ctx, denomID, tokenID, owner)
_, errCreator := k.IsDenomCreator(ctx, denomID, owner)
if errOwner != nil && errCreator != nil {
    return sdkerrors.Wrapf(types.ErrUnauthorized,
        "%s is neither the owner nor the creator of %s/%s", owner, denomID, tokenID)
}
```

Apply the same fix to `BurnNFT`.

### Proof of Concept

```go
// Keeper test (no mocks needed)
// 1. Alice issues denom
k.IssueDenom(ctx, "testdenom", "Test", "", "", alice)
// 2. Alice mints NFT to Bob
k.MintNFT(ctx, "testdenom", "nft1", "name", "uri", "data", alice, bob)
// 3. Bob tries to edit — must fail with ErrUnauthorized (IsOwner passes, IsDenomCreator fails)
err := k.EditNFT(ctx, "testdenom", "nft1", "newname", "[do-not-modify]", "[do-not-modify]", bob)
require.ErrorIs(t, err, types.ErrUnauthorized) // ← passes, confirming bug
// 4. Alice tries to edit — must also fail (IsOwner fails, Alice is not owner)
err = k.EditNFT(ctx, "testdenom", "nft1", "newname", "[do-not-modify]", "[do-not-modify]", alice)
require.ErrorIs(t, err, types.ErrUnauthorized) // ← passes, confirming metadata is permanently frozen
``` [5](#0-4)

### Citations

**File:** x/nft/keeper/keeper.go (L85-118)
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
