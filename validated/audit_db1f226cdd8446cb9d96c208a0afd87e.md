The code confirms the hypothesis. The `EditNFT` keeper function applies **both** `IsOwner` and `IsDenomCreator` as sequential AND-gated checks with no OR path.

---

### Title
NFT Metadata Permanently Frozen After Transfer — Both `IsOwner` AND `IsDenomCreator` Required Simultaneously in `EditNFT` - (`x/nft/keeper/keeper.go`)

### Summary
`Keeper.EditNFT` requires the caller to be simultaneously the current NFT owner **and** the denom creator. Once the creator transfers an NFT to any other address, no party satisfies both conditions, making the NFT's metadata permanently immutable.

### Finding Description

In `x/nft/keeper/keeper.go`, `EditNFT` enforces two sequential, independent authorization checks:

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

`IsOwner` verifies the caller equals `nft.Owner`: [2](#0-1) 

`IsDenomCreator` verifies the caller equals `denom.Creator`: [3](#0-2) 

After `MsgTransferNFT`, `nft.Owner` is updated to the recipient: [4](#0-3) 

The denom's `Creator` field is never updated. The result:

| Caller | `IsOwner` | `IsDenomCreator` | `EditNFT` result |
|---|---|---|---|
| Creator (pre-transfer) | ✓ | ✓ | succeeds |
| Creator (post-transfer) | ✗ | ✓ | `ErrUnauthorized` |
| New owner (post-transfer) | ✓ | ✗ | `ErrUnauthorized` |

The same AND-gate applies to `BurnNFT`: [5](#0-4) 

So after transfer, `MsgBurnNFT` is also permanently blocked for both parties.

### Impact Explanation
Any NFT transferred out of the creator's wallet has its metadata permanently frozen. No on-chain transaction can update `Name`, `URI`, or `Data` fields. For NFTs whose economic value depends on mutable metadata (gaming items, dynamic art, credentials), this constitutes a permanent lock of the NFT's economically valuable properties. The creator cannot recover the ability to edit without the recipient transferring back.

### Likelihood Explanation
The four-step sequence (`IssueDenom → MintNFT(recipient=creator) → TransferNFT → EditNFT`) is a normal, expected usage pattern. Any denom creator who mints and then sells/gifts an NFT triggers this state irreversibly. No special privileges or unusual conditions are required.

### Recommendation
Replace the AND-gate with an OR-gate: allow `EditNFT` if the caller is **either** the current owner **or** the denom creator. The intended design (denom creator controls metadata, owner controls transfer) requires OR semantics:

```go
isOwner := owner.Equals(nft.GetOwner())
_, creatorErr := k.IsDenomCreator(ctx, denomID, owner)
isDenomCreator := creatorErr == nil
if !isOwner && !isDenomCreator {
    return sdkerrors.Wrapf(types.ErrUnauthorized, ...)
}
```

### Proof of Concept

Execute in a keeper unit test against an unmodified chain:

1. `IssueDenom(ctx, "denom1", ..., creator)` — creator registers denom
2. `MintNFT(ctx, "denom1", "nft1", ..., creator, creator)` — creator mints to self
3. `TransferOwner(ctx, "denom1", "nft1", creator, user)` — creator transfers to user
4. `EditNFT(ctx, "denom1", "nft1", ..., creator)` — **returns `ErrUnauthorized`** (fails `IsOwner`)
5. `EditNFT(ctx, "denom1", "nft1", ..., user)` — **returns `ErrUnauthorized`** (fails `IsDenomCreator`)

Both assertions confirm the metadata is permanently frozen with no recovery path.

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

**File:** x/nft/keeper/keeper.go (L133-136)
```go
	nft.Owner = dstOwner.String()

	k.setNFT(ctx, denomID, nft)
	k.swapOwner(ctx, denomID, tokenID, srcOwner, dstOwner)
```

**File:** x/nft/keeper/keeper.go (L146-154)
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
