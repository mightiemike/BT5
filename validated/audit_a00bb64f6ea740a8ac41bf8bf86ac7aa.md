The code path is fully traceable and the invariant break is real. Here is the complete analysis:

---

### Title
Whitespace-Padded Denom Name Bypass Allows Duplicate Visually-Identical Collection Names — (`x/nft/types/validation.go`, `x/nft/keeper/denom.go`)

### Summary

`ValidateDenomName` trims whitespace on a **local copy** before the non-empty check, so names like `" foo "` pass validation. `SetDenom` and `HasDenomNm` then use the **raw, untrimmed** name as the KV-store key. Because `KeyDenomName(" foo ")` and `KeyDenomName("foo")` produce different byte keys, the uniqueness guard in `SetDenom` never fires for the second issuance, and two independently-owned denominations with visually identical display names can coexist on-chain.

### Finding Description

**Step 1 — `ValidateDenomName` trims locally but accepts the padded string:** [1](#0-0) 

`denomName = strings.TrimSpace(denomName)` modifies only the local variable. The function returns `nil` for `" foo "` because the trimmed value `"foo"` is non-empty. The caller's `msg.Name` is never sanitized.

**Step 2 — `ValidateBasic` for `MsgIssueDenom` delegates entirely to `ValidateDenomName`:** [2](#0-1) 

`" foo "` passes `ValidateBasic` without modification.

**Step 3 — `msgServer.IssueDenom` forwards the raw name unchanged:** [3](#0-2) 

**Step 4 — `Keeper.IssueDenom` passes the raw name into `SetDenom`:** [4](#0-3) 

**Step 5 — `SetDenom` checks uniqueness and stores using the raw name:** [5](#0-4) 

`HasDenomNm(ctx, " foo ")` returns `true` only if the key `KeyDenomName(" foo ")` exists. It returns `false` for `"foo"` because the keys differ at the byte level.

**Step 6 — `KeyDenomName` encodes the name verbatim:** [6](#0-5) 

`[]byte(" foo ")` ≠ `[]byte("foo")`, so the two store entries are completely independent.

### Impact Explanation

An attacker can:
1. Issue `denom1` with `name=" foo "` — passes `ValidateDenomName`, stored under key `\x05/ foo `.
2. Issue `denom2` with `name="foo"` — `HasDenomNm("foo")` returns `false` (key `\x05/foo` does not exist), so `SetDenom` succeeds and stores a second entry.

Both denominations now exist on-chain with display names that are visually indistinguishable to end users. The attacker who controls `denom2` can mint NFTs under the spoofed `"foo"` collection and sell them to buyers who believe they are purchasing from the legitimate `" foo "` collection (or vice versa). This is a concrete phishing vector with direct financial harm to NFT buyers.

### Likelihood Explanation

The attack requires only two standard `MsgIssueDenom` transactions signed by any two accounts (or even the same account with different denom IDs). No governance, privileged role, or special permission is needed. The denom IDs must differ (enforced by `ValidateDenomID`'s alphanumeric regex), but the names are unconstrained beyond non-emptiness after trimming. The attack is trivially reproducible on an unmodified local testnet.

### Recommendation

Normalize the denom name **before** any validation or storage. In `ValidateDenomName`, return an error if the input contains leading or trailing whitespace (or strip and return the normalized form for callers to use). Alternatively, trim `msg.Name` in `MsgIssueDenom.ValidateBasic` and propagate the trimmed value, or trim inside `Keeper.IssueDenom` before constructing the `Denom` struct. The fix must ensure that `SetDenom` and `HasDenomNm` always operate on the same normalized form that was validated.

### Proof of Concept

```go
// keeper unit test (pseudocode)
k.IssueDenom(ctx, "denom1", " foo ", "", "", creator1) // succeeds
assert.True(t, k.HasDenomNm(ctx, " foo "))
assert.False(t, k.HasDenomNm(ctx, "foo"))  // different key — guard does not fire

err := k.IssueDenom(ctx, "denom2", "foo", "", "", creator2)
assert.NoError(t, err)  // second issuance succeeds — invariant broken

d1, _ := k.GetDenomByName(ctx, " foo ")
d2, _ := k.GetDenomByName(ctx, "foo")
assert.NotEqual(t, d1.Id, d2.Id)  // two live denoms, visually identical names
```

### Citations

**File:** x/nft/types/validation.go (L57-63)
```go
func ValidateDenomName(denomName string) error {
	denomName = strings.TrimSpace(denomName)
	if len(denomName) == 0 {
		return sdkerrors.Wrapf(ErrInvalidDenomName, "denom name(%s) can not be space", denomName)
	}
	return nil
}
```

**File:** x/nft/types/msgs.go (L46-55)
```go
func (msg MsgIssueDenom) ValidateBasic() error {
	if err := ValidateDenomID(msg.Id); err != nil {
		return err
	}

	if _, err := sdk.AccAddressFromBech32(msg.Sender); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid sender address (%s)", err)
	}
	return ValidateDenomName(msg.Name)
}
```

**File:** x/nft/keeper/msg_server.go (L32-33)
```go
	if err := m.Keeper.IssueDenom(ctx, msg.Id, msg.Name, msg.Schema, msg.Uri, sender); err != nil {
		return nil, err
```

**File:** x/nft/keeper/keeper.go (L37-42)
```go
func (k Keeper) IssueDenom(ctx sdk.Context,
	id, name, schema, uri string,
	creator sdk.AccAddress,
) error {
	return k.SetDenom(ctx, types.NewDenom(id, name, schema, uri, creator))
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

**File:** x/nft/types/keys.go (L125-128)
```go
func KeyDenomName(name string) []byte {
	key := append(PrefixDenomName, delimiter...)
	return append(key, []byte(name)...)
}
```
