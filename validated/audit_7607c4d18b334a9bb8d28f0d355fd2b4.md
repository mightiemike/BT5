Based on my analysis of the production code, I can now give a definitive answer.

---

### Title
Prefix Collision: `KeyNFT('ibc','')` Iterates Over All IBC-Transfer NFTs — (`x/nft/types/keys.go`, `x/nft/keeper/nft.go`, `x/nft/keeper/collection.go`)

### Summary

An unprivileged attacker can issue a denom with ID `'ibc'` via `MsgIssueDenom`. Because `KeyNFT('ibc','')` produces the byte prefix `0x01/ibc/`, which is a strict byte-prefix of every IBC-transfer NFT key `0x01/ibc/<HASH64>/<tokenID>`, the `KVStorePrefixIterator` in `GetNFTs(ctx, 'ibc')` iterates over all IBC-owned NFTs. This causes `GetTotalSupply('ibc')` to diverge from `len(GetNFTs(ctx,'ibc'))`, and `ExportGenesis`/`GetCollections` to attribute IBC NFTs to the attacker-controlled `'ibc'` denom — producing duplicate NFTs in the genesis export.

---

### Finding Description

**Step 1 — Attacker issues denom `'ibc'`**

`MsgIssueDenom.ValidateBasic()` calls `ValidateDenomID(msg.Id)`: [1](#0-0) 

`ValidateDenomID` only checks length ∈ [3,64], begins with alpha, and is lowercase alphanumeric: [2](#0-1) 

`'ibc'` is 3 chars, begins with `i`, all lowercase alpha — it passes. Crucially, `ValidateDenomID` is used here, **not** `ValidateDenomIDWithIBC` (which enforces the `ibc/<64-hex>` format). The keeper's `SetDenom` adds no further guard — it only rejects duplicate IDs: [3](#0-2) 

**Step 2 — Key construction produces a colliding prefix**

`KeyNFT` builds keys as `PrefixNFT + "/" + denomID + "/" [+ tokenID]`: [4](#0-3) 

- `KeyNFT('ibc', '')` → `[0x01, '/', 'i', 'b', 'c', '/']`
- `KeyNFT('ibc/DEADBEEF…64hex', 'tok1')` → `[0x01, '/', 'i', 'b', 'c', '/', 'D', 'E', 'A', 'D', …, '/', 't', 'o', 'k', '1']`

The first key is a **byte-prefix** of the second. `PrefixNFT = []byte{0x01}` and `delimiter = []byte{"/"}`: [5](#0-4) 

**Step 3 — `GetNFTs(ctx, 'ibc')` iterates over IBC NFTs**

`GetNFTs` uses `KVStorePrefixIterator` with `KeyNFT(denom, "")` as the scan prefix: [6](#0-5) 

With `denom='ibc'`, the iterator scans all keys ≥ `0x01/ibc/`, which includes every IBC-transfer NFT stored as `0x01/ibc/<HASH>/<tokenID>`. The values are `BaseNFT` protobuf blobs — identical encoding for both native and IBC NFTs — so they unmarshal without error.

**Step 4 — Supply counter diverges**

`GetTotalSupply` reads a single key `KeyCollection('ibc')` = `[0x03, '/', 'i', 'b', 'c']`: [7](#0-6) 

`increaseSupply` only increments this counter when an NFT is explicitly minted into the `'ibc'` denom: [8](#0-7) 

IBC mints go into `'ibc/<HASH>'` denoms and increment `KeyCollection('ibc/<HASH>')`, never `KeyCollection('ibc')`. So:

- `GetTotalSupply('ibc')` = 0 (or N if attacker minted N NFTs)
- `len(GetNFTs(ctx, 'ibc'))` = N + M (where M = all IBC NFTs on chain)

The invariant `GetTotalSupply == len(GetNFTs)` is broken.

**Step 5 — `ExportGenesis` / `GetCollections` corruption**

`GetCollections` iterates all registered denoms and calls `GetNFTs(ctx, denom.Id)` for each: [9](#0-8) 

`GetCollection` (used by query handlers) does the same: [10](#0-9) 

`GetPaginateCollection` (used by the paginated gRPC query) also uses the same prefix store: [11](#0-10) 

Result: every IBC NFT appears **twice** in the genesis export — once correctly under `'ibc/<HASH>'` and once incorrectly under the attacker-controlled `'ibc'` denom. On re-import, `SetGenesisCollection` would attempt to mint the same NFTs again, causing a collision or double-mint depending on error handling.

---

### Impact Explanation

| Invariant | Expected | Actual (after attack) |
|---|---|---|
| `GetTotalSupply('ibc')` | = `len(GetNFTs(ctx,'ibc'))` | Diverges by M (all IBC NFTs) |
| Genesis export uniqueness | Each NFT in exactly one collection | IBC NFTs appear in both `'ibc'` and `'ibc/<HASH>'` |
| Collection query for `'ibc'` | Returns only attacker's NFTs | Returns all IBC NFTs on chain |

The genesis export corruption is the most severe consequence: a chain halt + restart using the exported genesis would attempt to re-mint all IBC NFTs under the `'ibc'` denom, breaking the IBC NFT accounting entirely.

---

### Likelihood Explanation

The attack requires only a single `MsgIssueDenom` transaction with `Id='ibc'`, which any account with gas can submit. No governance, no special permissions, no operator compromise. The only prerequisite is that IBC NFTs exist on chain (or will exist after the denom is registered). The denom `'ibc'` is not reserved anywhere in the production code.

---

### Recommendation

1. **In `ValidateDenomID`**: reject any denom ID that equals `"ibc"` or starts with `"ibc/"`:
   ```go
   if denomID == IBCPrefix[:3] || strings.HasPrefix(denomID, IBCPrefix) {
       return sdkerrors.Wrapf(ErrInvalidDenom, "denom id cannot be 'ibc' or start with 'ibc/'")
   }
   ``` [2](#0-1) 

2. **In `KeyNFT`**: use a length-prefixed or null-terminated encoding for `denomID` to prevent prefix collisions between `'ibc'` and `'ibc/<HASH>'`.

3. **In `SplitKeyDenom`**: the existing IBC-aware split logic already handles this correctly for the owner index — apply the same awareness to the NFT prefix iterator.

---

### Proof of Concept

```go
// keeper integration test (no mocks, standard testutil setup)
func TestIBCPrefixCollision(t *testing.T) {
    ctx, k := setupKeeper(t)

    // Attacker issues denom 'ibc' — passes ValidateDenomID (len=3, alpha)
    err := k.IssueDenom(ctx, "ibc", "ibc-attacker", "", "", attackerAddr)
    require.NoError(t, err)

    // Simulate IBC receive: mint NFT under 'ibc/<64-hex-hash>'
    ibcDenom := "ibc/" + strings.Repeat("A", 64) // valid IBCDenomLen=68
    err = k.IssueDenom(ctx, ibcDenom, "ibc-voucher", "", "", escrowAddr)
    require.NoError(t, err)
    err = k.MintNFTUnverified(ctx, ibcDenom, "tok1", "", "", "", userAddr)
    require.NoError(t, err)

    // Supply for 'ibc' denom = 0 (attacker minted nothing)
    require.Equal(t, uint64(0), k.GetTotalSupply(ctx, "ibc"))

    // But GetNFTs('ibc') returns the IBC NFT due to prefix collision
    nfts := k.GetNFTs(ctx, "ibc")
    require.Equal(t, 1, len(nfts)) // BUG: returns IBC NFT, not 0

    // Invariant violated: supply(0) != len(GetNFTs)(1)
    // ExportGenesis will attribute the IBC NFT to the 'ibc' denom
}
``` [6](#0-5) [4](#0-3) [12](#0-11)

### Citations

**File:** x/nft/types/msgs.go (L46-48)
```go
func (msg MsgIssueDenom) ValidateBasic() error {
	if err := ValidateDenomID(msg.Id); err != nil {
		return err
```

**File:** x/nft/types/validation.go (L12-38)
```go
const (
	DoNotModify = "[do-not-modify]"
	MinDenomLen = 3
	MaxDenomLen = 64
	IBCDenomLen = 68
	IBCPrefix   = "ibc/"

	MaxTokenURILen = 256
)

var (
	// IsAlphaNumeric only accepts [a-z0-9]
	IsAlphaNumeric = regexp.MustCompile(`^[a-z0-9]+$`).MatchString
	// IsBeginWithAlpha only begin with [a-z]
	IsBeginWithAlpha = regexp.MustCompile(`^[a-z].*`).MatchString
)

// ValidateDenomID verifies whether the parameters are legal.
func ValidateDenomID(denomID string) error {
	if len(denomID) < MinDenomLen || len(denomID) > MaxDenomLen {
		return sdkerrors.Wrapf(ErrInvalidDenom, "the length of denom(%s) only accepts value [%d, %d]", denomID, MinDenomLen, MaxDenomLen)
	}
	if !IsBeginWithAlpha(denomID) || !IsAlphaNumeric(denomID) {
		return sdkerrors.Wrapf(ErrInvalidDenom, "the denom(%s) only accepts lowercase alphanumeric characters, and begin with an english letter", denomID)
	}
	return nil
}
```

**File:** x/nft/keeper/denom.go (L26-39)
```go
func (k Keeper) SetDenom(ctx sdk.Context, denom types.Denom) error {
	if k.HasDenomID(ctx, denom.Id) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomID %s has already exists", denom.Id)
	}

	if k.HasDenomNm(ctx, denom.Name) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomName %s has already exists", denom.Name)
	}

	store := ctx.KVStore(k.storeKey)
	bz := k.cdc.MustMarshal(&denom)
	store.Set(types.KeyDenomID(denom.Id), bz)
	store.Set(types.KeyDenomName(denom.Name), []byte(denom.Id))
	return nil
```

**File:** x/nft/types/keys.go (L29-36)
```go
	PrefixNFT        = []byte{0x01}
	PrefixOwners     = []byte{0x02} // key for a owner
	PrefixCollection = []byte{0x03} // key for balance of NFTs held by the denom
	PrefixDenom      = []byte{0x04} // key for denom of the nft
	PrefixDenomName  = []byte{0x05} // key for denom name of the nft

	delimiter = []byte("/")
)
```

**File:** x/nft/types/keys.go (L99-110)
```go
func KeyNFT(denomID, tokenID string) []byte {
	key := append(PrefixNFT, delimiter...)
	if len(denomID) > 0 {
		key = append(key, []byte(denomID)...)
		key = append(key, delimiter...)
	}

	if len(denomID) > 0 && len(tokenID) > 0 {
		key = append(key, []byte(tokenID)...)
	}
	return key
}
```

**File:** x/nft/keeper/nft.go (L30-41)
```go
func (k Keeper) GetNFTs(ctx sdk.Context, denom string) (nfts []exported.NFT) {
	store := ctx.KVStore(k.storeKey)
	iterator := storetypes.KVStorePrefixIterator(store, types.KeyNFT(denom, ""))
	defer iterator.Close()
	for ; iterator.Valid(); iterator.Next() {
		var baseNFT types.BaseNFT
		k.cdc.MustUnmarshal(iterator.Value(), &baseNFT)
		nfts = append(nfts, baseNFT)
	}

	return nfts
}
```

**File:** x/nft/keeper/collection.go (L58-66)
```go
func (k Keeper) GetCollection(ctx sdk.Context, denomID string) (types.Collection, error) {
	denom, err := k.GetDenom(ctx, denomID)
	if err != nil {
		return types.Collection{}, sdkerrors.Wrapf(types.ErrInvalidDenom, "denomID %s not existed ", denomID)
	}

	nfts := k.GetNFTs(ctx, denomID)
	return types.NewCollection(denom, nfts), nil
}
```

**File:** x/nft/keeper/collection.go (L75-87)
```go
	store := ctx.KVStore(k.storeKey)
	nftStore := prefix.NewStore(store, types.KeyNFT(denomID, ""))
	pageRes, err := query.Paginate(nftStore, request.Pagination, func(key, value []byte) error {
		var baseNFT types.BaseNFT
		k.cdc.MustUnmarshal(value, &baseNFT)
		nfts = append(nfts, baseNFT)
		return nil
	})
	if err != nil {
		return types.Collection{}, nil, status.Errorf(codes.InvalidArgument, "paginate: %v", err)
	}
	return types.NewCollection(denom, nfts), pageRes, nil
}
```

**File:** x/nft/keeper/collection.go (L90-97)
```go
func (k Keeper) GetCollections(ctx sdk.Context) (cs []types.Collection) {
	denoms := k.GetDenoms(ctx)
	cs = make([]types.Collection, 0, len(denoms))
	for _, denom := range denoms {
		cs = append(cs, types.NewCollection(denom, k.GetNFTs(ctx, denom.Id)))
	}
	return cs
}
```

**File:** x/nft/keeper/collection.go (L99-107)
```go
// GetTotalSupply returns the number of NFTs by the specified denom ID
func (k Keeper) GetTotalSupply(ctx sdk.Context, denomID string) uint64 {
	store := ctx.KVStore(k.storeKey)
	bz := store.Get(types.KeyCollection(denomID))
	if len(bz) == 0 {
		return 0
	}
	return types.MustUnMarshalSupply(k.cdc, bz)
}
```

**File:** x/nft/keeper/collection.go (L120-127)
```go
func (k Keeper) increaseSupply(ctx sdk.Context, denomID string) {
	supply := k.GetTotalSupply(ctx, denomID)
	supply++

	store := ctx.KVStore(k.storeKey)
	bz := types.MustMarshalSupply(k.cdc, supply)
	store.Set(types.KeyCollection(denomID), bz)
}
```
