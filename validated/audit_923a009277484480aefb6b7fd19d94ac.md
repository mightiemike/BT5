### Title
Case-Sensitive Denom Name Uniqueness Check Allows Duplicate NFT Denom Registration - (File: x/nft/keeper/denom.go)

### Summary
The `SetDenom` function in `x/nft/keeper/denom.go` checks for duplicate denom names using a raw, case-sensitive KV store key. Because `ValidateDenomName` imposes no case restriction on the `name` field, any unprivileged user can issue two NFT denoms whose names differ only in case (e.g., `"MyNFT"` and `"mynft"`), bypassing the protocol's stated invariant that denom names must be globally unique.

### Finding Description

`MsgIssueDenom` is a permissionless transaction available to any account. Its `ValidateBasic` calls `ValidateDenomID` for the `id` field and `ValidateDenomName` for the `name` field.

`ValidateDenomID` enforces lowercase-only alphanumeric characters via the regex `^[a-z0-9]+$`, making the ID uniqueness check effectively case-insensitive by construction. [1](#0-0) 

`ValidateDenomName`, however, only rejects empty strings after trimming whitespace. It applies no case normalization or restriction: [2](#0-1) 

Inside `SetDenom`, the name uniqueness check calls `HasDenomNm`, which stores and looks up the name as a raw byte key with no normalization: [3](#0-2) 

The KV key is constructed directly from the raw name string: [4](#0-3) 

This means `"MyNFT"` and `"mynft"` produce different store keys, so `HasDenomNm` returns `false` for the second registration even though a semantically identical name already exists.

The full call path from a signed transaction is:

`MsgIssueDenom` → `msgServer.IssueDenom` → `Keeper.IssueDenom` → `SetDenom` → `HasDenomNm` (case-sensitive miss) [5](#0-4) [6](#0-5) 

The spec explicitly states: *"both, `Id` and `Name`, are required to be unique globally."* [7](#0-6) 

### Impact Explanation

The global uniqueness invariant for denom names is broken. Two denoms with names differing only in case (e.g., `"CryptoArt"` and `"cryptoart"`) can coexist on-chain. The `GetDenomByName` query returns different results for each casing, so applications and users querying by name will silently resolve to different denoms. A malicious actor can register a near-identical name to an established NFT collection, causing users to mint NFTs under the wrong denom — fragmenting the collection's supply and ownership across two separate denom IDs. NFT ownership records, supply counts, and collection membership are all tracked per denom ID, so NFTs minted under the impersonating denom are permanently separated from the legitimate collection.

**Impact: 3 | Likelihood: 3**

### Likelihood Explanation

The entry point is a standard, permissionless `MsgIssueDenom` transaction requiring no special role or privilege. Any account with enough gas can trigger this. The only precondition is that a target denom name already exists on-chain, which is publicly queryable. The attack requires no leaked keys, no social engineering, and no privileged access.

### Recommendation

Normalize the denom name to a canonical case (e.g., `strings.ToLower(denom.Name)`) before the `HasDenomNm` check and before storing it with `KeyDenomName`. Alternatively, enforce in `ValidateDenomName` that names must be lowercase-only, consistent with the restriction already applied to denom IDs in `ValidateDenomID`.

### Proof of Concept

1. Alice submits `MsgIssueDenom{Id: "mytoken1", Name: "CryptoArt", Sender: alice}` — succeeds, denom stored under key `KeyDenomName("CryptoArt")`.
2. Bob submits `MsgIssueDenom{Id: "mytoken2", Name: "cryptoart", Sender: bob}` — `HasDenomNm(ctx, "cryptoart")` checks key `KeyDenomName("cryptoart")`, which does not exist, so the check passes and the second denom is registered.
3. `GetDenomByName("CryptoArt")` returns Alice's denom; `GetDenomByName("cryptoart")` returns Bob's denom.
4. Users who query by the lowercase name mint NFTs under Bob's denom ID `"mytoken2"`, permanently separated from Alice's legitimate `"mytoken1"` collection.

The broken invariant is confirmed by the fact that `ValidateDenomName` accepts any non-empty string: [2](#0-1) 

while `SetDenom` stores the name verbatim: [8](#0-7)

### Citations

**File:** x/nft/types/validation.go (L23-37)
```go
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
```

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

**File:** x/nft/keeper/denom.go (L20-39)
```go
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

**File:** x/nft/keeper/msg_server.go (L25-51)
```go
func (m msgServer) IssueDenom(goCtx context.Context, msg *types.MsgIssueDenom) (*types.MsgIssueDenomResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.IssueDenom(ctx, msg.Id, msg.Name, msg.Schema, msg.Uri, sender); err != nil {
		return nil, err
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeIssueDenom,
			sdk.NewAttribute(types.AttributeKeyDenomID, msg.Id),
			sdk.NewAttribute(types.AttributeKeyDenomName, msg.Name),
			sdk.NewAttribute(types.AttributeKeyCreator, msg.Sender),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.AttributeValueCategory),
			sdk.NewAttribute(sdk.AttributeKeySender, msg.Sender),
		),
	})

	return &types.MsgIssueDenomResponse{}, nil
}
```

**File:** x/nft/keeper/keeper.go (L36-42)
```go
// IssueDenom issues a denom according to the given params
func (k Keeper) IssueDenom(ctx sdk.Context,
	id, name, schema, uri string,
	creator sdk.AccAddress,
) error {
	return k.SetDenom(ctx, types.NewDenom(id, name, schema, uri, creator))
}
```

**File:** x/nft/spec/02_messages.md (L8-9)
```markdown
This message defines a type of non-fungible tokens, there can be multiple non-fungible tokens of the same type. Note
that both, `Id` and `Name`, are required to be unique globally.
```
