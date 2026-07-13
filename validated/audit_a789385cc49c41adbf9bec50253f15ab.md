### Title
NFT Denom Name Uniqueness Check Is Case-Sensitive, Enabling Collection Impersonation via `MsgIssueDenom` — (File: `x/nft/keeper/denom.go`, `x/nft/types/validation.go`)

---

### Summary

`ValidateDenomName` accepts any non-empty string without case normalization. `SetDenom` stores and checks denom names using raw byte keys, making the uniqueness check case-sensitive. An unprivileged attacker can register a denom whose `Name` field differs only in letter casing from an existing legitimate denom name. Because `GetDenomByName` is a case-sensitive lookup, a client that normalizes names to lowercase before querying will resolve to the attacker's denom instead of the legitimate one, enabling NFT collection impersonation.

---

### Finding Description

The `MsgIssueDenom` message carries two globally-unique identifiers: `Id` (the denom ID) and `Name` (the denom name). The spec explicitly states: *"both, `Id` and `Name`, are required to be unique globally."*

`ValidateDenomID` enforces strict lowercase-alphanumeric format via `^[a-z0-9]+$`, so denom IDs are inherently normalized.

`ValidateDenomName`, however, only rejects empty/whitespace strings:

```go
// x/nft/types/validation.go
func ValidateDenomName(denomName string) error {
    denomName = strings.TrimSpace(denomName)
    if len(denomName) == 0 {
        return sdkerrors.Wrapf(ErrInvalidDenomName, "denom name(%s) can not be space", denomName)
    }
    return nil
}
``` [1](#0-0) 

`SetDenom` then stores the name as a raw byte key and checks uniqueness against that exact byte sequence:

```go
// x/nft/keeper/denom.go
func (k Keeper) HasDenomNm(ctx sdk.Context, name string) bool {
    store := ctx.KVStore(k.storeKey)
    return store.Has(types.KeyDenomName(name))
}

func (k Keeper) SetDenom(ctx sdk.Context, denom types.Denom) error {
    ...
    if k.HasDenomNm(ctx, denom.Name) {
        return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomName %s has already exists", denom.Name)
    }
    store.Set(types.KeyDenomName(denom.Name), []byte(denom.Id))
    ...
}
``` [2](#0-1) 

`KeyDenomName` uses the raw string bytes as the store key:

```go
func KeyDenomName(name string) []byte {
    key := append(PrefixDenomName, delimiter...)
    return append(key, []byte(name)...)
}
``` [3](#0-2) 

Because the key is the raw byte representation of the name, `"CryptoKitties"` and `"cryptokitties"` produce different keys and both pass the `HasDenomNm` guard. The protocol's own test confirms this is intentional at the runtime level:

```go
// x/nft/keeper/genesis_test.go
err := suite.keeper.IssueDenom(suite.ctx, "poisoneddenom", "Poisoned Denom Name", schema, "", address)
suite.NoError(err, "runtime accepts denom names with spaces/uppercase")
``` [4](#0-3) 

The `GetDenomByName` query path is fully exposed on-chain via gRPC and CLI:

```go
// x/nft/client/cli/query.go
if err := types.ValidateDenomName(args[0]); err != nil { return err }
queryClient.DenomByName(context.Background(), &types.QueryDenomByNameRequest{DenomName: args[0]})
``` [5](#0-4) 

---

### Impact Explanation

**Corrupted invariant**: The protocol specification guarantees that `Name` is globally unique. The case-sensitive uniqueness check breaks this guarantee: `"CryptoKitties"` and `"cryptokitties"` are treated as distinct names and both can be registered under different denom IDs.

**Concrete attack scenario**:

1. Legitimate creator registers denom ID `cryptokitties` with name `"CryptoKitties"`.
2. Attacker registers denom ID `cryptokittiez` with name `"cryptokitties"` (all lowercase).
3. A wallet or marketplace normalizes user input to lowercase before calling `GetDenomByName("cryptokitties")`.
4. The query returns the attacker's denom (`cryptokittiez`), not the legitimate one (`cryptokitties`).
5. Users who mint or purchase NFTs through this path receive tokens from the attacker's collection, not the legitimate one.

The corrupted value is the denom returned by `GetDenomByName`: it resolves to the attacker-controlled denom ID, causing NFT ownership records to be written under the wrong collection. Users who pay marketplace fees or gas to mint under the impersonating denom receive worthless NFTs.

---

### Likelihood Explanation

**Medium.** The attack requires a legitimate high-value NFT collection to exist first. The attacker then submits a standard `MsgIssueDenom` transaction — no special permissions required. Any wallet, marketplace, or indexer that normalizes denom names before querying (a common and reasonable practice) is vulnerable to the misdirection. The `GetDenomByName` endpoint is publicly accessible via gRPC and CLI.

---

### Recommendation

Normalize denom names to a canonical form (e.g., lowercase) inside `ValidateDenomName` before the uniqueness check, or perform a case-insensitive lookup in `HasDenomNm`. The simplest fix is to add `strings.ToLower` normalization in `ValidateDenomName` and apply the same normalization before storing and looking up the name key in `SetDenom` and `GetDenomByName`.

---

### Proof of Concept

```
# Step 1: Legitimate creator registers denom with mixed-case name
chain-maind tx nft issue cryptokitties \
  --name="CryptoKitties" --from=creator --chain-id=...

# Step 2: Attacker registers a different denom ID with the lowercase variant of the name
chain-maind tx nft issue cryptokittiez \
  --name="cryptokitties" --from=attacker --chain-id=...
# → succeeds: HasDenomNm("cryptokitties") is false because "CryptoKitties" ≠ "cryptokitties"

# Step 3: Client normalizes name to lowercase and queries
chain-maind query nft denom-by-name "cryptokitties"
# → returns denom ID "cryptokittiez" (attacker's collection), NOT "cryptokitties"

# Step 4: User mints NFT under the resolved denom ID, receiving a token
# from the attacker's collection instead of the legitimate one
chain-maind tx nft mint tokenxyz cryptokittiez \
  --from=user --recipient=user --chain-id=...
```

The `ValidateDenomName` call at line 240 of `x/nft/client/cli/query.go` accepts both `"CryptoKitties"` and `"cryptokitties"` as valid inputs, and `GetDenomByName` performs a byte-exact lookup, returning whichever denom was registered under that exact casing — which is the attacker's denom in this scenario. [1](#0-0) [6](#0-5) [5](#0-4)

### Citations

**File:** x/nft/types/validation.go (L56-63)
```go
// ValidateDenomName verifies whether the parameters are legal.
func ValidateDenomName(denomName string) error {
	denomName = strings.TrimSpace(denomName)
	if len(denomName) == 0 {
		return sdkerrors.Wrapf(ErrInvalidDenomName, "denom name(%s) can not be space", denomName)
	}
	return nil
}
```

**File:** x/nft/keeper/denom.go (L19-39)
```go
// HasDenomNm returns whether the specified denom name exists
func (k Keeper) HasDenomNm(ctx sdk.Context, name string) bool {
	store := ctx.KVStore(k.storeKey)
	return store.Has(types.KeyDenomName(name))
}

// SetDenom is responsible for saving the definition of denom
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

**File:** x/nft/types/keys.go (L124-128)
```go
// KeyDenomName gets the storeKey by the denom name
func KeyDenomName(name string) []byte {
	key := append(PrefixDenomName, delimiter...)
	return append(key, []byte(name)...)
}
```

**File:** x/nft/keeper/genesis_test.go (L11-13)
```go
func (suite *KeeperSuite) TestExportInitGenesisRoundTripFreeFormName() {
	err := suite.keeper.IssueDenom(suite.ctx, "poisoneddenom", "Poisoned Denom Name", schema, "", address)
	suite.NoError(err, "runtime accepts denom names with spaces/uppercase")
```

**File:** x/nft/client/cli/query.go (L240-248)
```go
			if err := types.ValidateDenomName(args[0]); err != nil {
				return err
			}

			queryClient := types.NewQueryClient(clientCtx)
			resp, err := queryClient.DenomByName(
				context.Background(),
				&types.QueryDenomByNameRequest{DenomName: args[0]},
			)
```
