I have all the code needed to evaluate this claim. Let me trace through the exact logic.

The code trace is complete. All relevant logic is confirmed.

---

### Title
Genesis Validation Accepts `DecayRate > 0` with `DecayEpochStart = 0`, Permanently Disabling Inflation Decay — (`x/inflation/types/genesis.go`, `x/inflation/keeper/genesis.go`, `x/inflation/keeper/mint.go`)

### Summary

A one-sided validation gap in `GenesisState.Validate()` allows a genesis file with a positive `DecayRate` and a zero `DecayEpochStart` to pass validation. `InitGenesis` then silently skips persisting the epoch start because it treats `0` as a sentinel for "not set." As a result, `DeflationCalculationFn` can never find the epoch start in the KV store and permanently returns `baseRate`, meaning the configured decay never activates and more tokens are minted than the economic schedule intends for the entire chain lifetime.

### Finding Description

**Step 1 — Validation gap.**

`GenesisState.Validate()` only checks one direction of the invariant:

```go
// x/inflation/types/genesis.go:19
if !gs.Params.DecayRate.IsPositive() && gs.DecayEpochStart != 0 {
    return fmt.Errorf("decay_epoch_start must be zero when decay is disabled")
}
```

This rejects `DecayRate ≤ 0 AND DecayEpochStart ≠ 0`, but it never rejects `DecayRate > 0 AND DecayEpochStart == 0`. A genesis file with `DecayRate = 0.065` and `DecayEpochStart = 0` passes without error. [1](#0-0) 

**Step 2 — Silent skip in `InitGenesis`.**

```go
// x/inflation/keeper/genesis.go:19-23
if genState.DecayEpochStart != 0 {
    if err := k.SetDecayEpochStart(ctx, genState.DecayEpochStart); err != nil {
        panic(err)
    }
}
```

Because `DecayEpochStart == 0`, the branch is never entered and `SetDecayEpochStart` is never called. The `DecayEpochStartKey` is never written to the KV store. [2](#0-1) 

**Step 3 — `getDecayEpochStart` returns `ok=false`.**

```go
// x/inflation/keeper/keeper.go:104-106
if len(bz) == 0 {
    return 0, false, nil
}
```

Because the key was never stored, every call returns `(0, false, nil)`. [3](#0-2) 

**Step 4 — `DeflationCalculationFn` falls back to `baseRate` forever.**

```go
// x/inflation/keeper/mint.go:34-44
if !decayRate.IsPositive() {
    return baseRate
}
decayEpoch, ok, err := k.getDecayEpochStart(ctx)
...
if !ok {
    return baseRate   // ← reached every block, decay never applied
}
```

`decayRate` is `0.065` (positive), so the first guard is skipped. But `ok=false` triggers the second guard, returning `baseRate` on every block for the entire chain lifetime. [4](#0-3) 

### Impact Explanation

The configured exponential decay schedule is completely bypassed. Inflation stays at `baseRate` indefinitely instead of decaying by ~6.5% per month. Over a multi-year chain lifetime this produces a materially larger token supply than the economic model intends — unbacked token issuance that dilutes all non-staking holders and violates the supply schedule encoded in the genesis parameters. If `MaxSupply` is also set, the chain will eventually panic and halt when the inflated supply crosses the cap (`BeginBlocker` panics on `totalsupply > maxsupply`). [5](#0-4) 

### Likelihood Explanation

The path is reachable through the standard genesis initialization flow with no special privileges beyond authoring the genesis file (chain launch or upgrade genesis). The misconfiguration passes all existing validation silently, so it can be introduced by mistake or by a malicious genesis author. No governance vote or key compromise is required after the genesis is accepted.

### Recommendation

Add the inverse guard to `GenesisState.Validate()`:

```go
if gs.Params.DecayRate.IsPositive() && gs.DecayEpochStart == 0 {
    return fmt.Errorf("decay_epoch_start must be non-zero when decay_rate is positive")
}
```

Alternatively, treat `DecayEpochStart == 0` as "start decay at genesis height 1" and always store it when `DecayRate > 0`, removing the sentinel-value ambiguity entirely. [1](#0-0) 

### Proof of Concept

```go
// keeper genesis test
genState := types.GenesisState{
    Params: types.NewParams(
        sdkmath.NewInt(0),   // unlimited supply
        []string{},
        sdkmath.LegacyMustNewDecFromStr("0.065"), // positive decay rate
    ),
    DecayEpochStart: 0, // zero — passes Validate(), skipped by InitGenesis
}
// genState.Validate() returns nil — no error
k.InitGenesis(ctx, genState)

// Advance 1000 blocks
for i := 0; i < 1000; i++ {
    ctx = ctx.WithBlockHeight(ctx.BlockHeight() + 1)
    inflation := k.DeflationCalculationFn()(ctx, minter, mintParams, bondedRatio)
    // inflation == baseRate every block; decay never applied
    assert(inflation.Equal(baseRate))
}
```

The assertion holds for every block because `getDecayEpochStart` always returns `ok=false`, so `DeflationCalculationFn` always returns `baseRate` regardless of the configured `DecayRate`. [6](#0-5)

### Citations

**File:** x/inflation/types/genesis.go (L14-23)
```go
func (gs GenesisState) Validate() error {
	if err := gs.Params.Validate(); err != nil {
		return err
	}

	if !gs.Params.DecayRate.IsPositive() && gs.DecayEpochStart != 0 {
		return fmt.Errorf("decay_epoch_start must be zero when decay is disabled")
	}

	return nil
```

**File:** x/inflation/keeper/genesis.go (L19-23)
```go
	if genState.DecayEpochStart != 0 {
		if err := k.SetDecayEpochStart(ctx, genState.DecayEpochStart); err != nil {
			panic(err)
		}
	}
```

**File:** x/inflation/keeper/keeper.go (L98-111)
```go
func (k Keeper) getDecayEpochStart(ctx context.Context) (uint64, bool, error) {
	store := k.storeService.OpenKVStore(ctx)
	bz, err := store.Get([]byte(types.DecayEpochStartKey))
	if err != nil {
		return 0, false, err
	}
	if len(bz) == 0 {
		return 0, false, nil
	}
	if len(bz) != 8 {
		return 0, false, fmt.Errorf("invalid decay epoch start encoding: len=%d", len(bz))
	}
	return binary.BigEndian.Uint64(bz), true, nil
}
```

**File:** x/inflation/keeper/mint.go (L34-44)
```go
		if !decayRate.IsPositive() {
			return baseRate
		}

		decayEpoch, ok, err := k.getDecayEpochStart(ctx)
		if err != nil {
			panic(fmt.Sprintf("failed to get decay epoch start: %s", err))
		}
		if !ok {
			return baseRate
		}
```

**File:** x/inflation/abci.go (L37-39)
```go
	if maxsupply.IsPositive() && totalsupply.GT(maxsupply) {
		panic(fmt.Sprintf("the total supply has exceeded the maximum supply: %s > %s", totalsupply, maxsupply))
	}
```
