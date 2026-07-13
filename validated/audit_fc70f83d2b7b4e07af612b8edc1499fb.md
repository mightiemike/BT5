[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** x/inflation/types/expected_keepers.go (L9-12)
```go
type BankKeeper interface {
	GetSupply(ctx context.Context, denom string) sdk.Coin
	GetBalance(ctx context.Context, addr sdk.AccAddress, denom string) sdk.Coin
}
```

**File:** x/inflation/keeper/mint.go (L20-30)
```go
func (k *Keeper) DeflationCalculationFn() func(ctx context.Context, minter minttypes.Minter, params minttypes.Params, bondedRatio math.LegacyDec) math.LegacyDec {
	return func(ctx context.Context, minter minttypes.Minter, params minttypes.Params, bondedRatio math.LegacyDec) math.LegacyDec {
		inflationParams, err := k.GetParams(ctx)
		if err != nil {
			panic(fmt.Sprintf("failed to get inflation params: %s", err))
		}
		decayRate := inflationParams.DecayRate

		// Calculate base inflation rate using default method
		baseRate := minttypes.DefaultInflationCalculationFn(ctx, minter, params, bondedRatio)

```

**File:** x/inflation/types/keys.go (L3-9)
```go
const (
	// ModuleName defines the module name
	ModuleName = "inflation"

	// StoreKey defines the primary module store key
	StoreKey = ModuleName
)
```
