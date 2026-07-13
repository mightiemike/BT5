Now I have all the code I need to make a definitive determination. Let me trace the exact path:

The code path is fully traceable. Here is the determination:

---

### Title
Missing Inverse Validation in `GenesisState.Validate` Silently Disables Configured Decay — (`x/inflation/types/genesis.go`)

### Summary

`GenesisState.Validate()` enforces only one direction of the `DecayRate`/`DecayEpochStart` invariant. A genesis file with `DecayRate > 0` and `DecayEpochStart = 0` passes validation, `InitGenesis` never writes the epoch key, and `DeflationCalculationFn` returns `baseRate` forever — decay is permanently disabled despite being configured.

---

### Finding Description

**Step 1 — Validation passes silently.**

`GenesisState.Validate()` contains a single cross-field check:

```go
if !gs.Params.DecayRate.IsPositive() && gs.DecayEpochStart != 0 {
    return fmt.Errorf("decay_epoch_start must be zero when decay is disabled")
}
``` [1](#0-0) 

The condition is `!IsPositive(DecayRate) && DecayEpochStart != 0`. When `DecayRate = 0.065` (positive) and `DecayEpochStart = 0`, the left operand is `false`, so the whole expression short-circuits to `false`. No error is returned. The inverse invariant — *if `DecayRate > 0`, then `DecayEpochStart` must be non-zero* — is never checked.

**Step 2 — `InitGenesis` skips writing the epoch key.**

```go
if genState.DecayEpochStart != 0 {
    if err := k.SetDecayEpochStart(ctx, genState.DecayEpochStart); err != nil {
        panic(err)
    }
}
``` [2](#0-1) 

Because `DecayEpochStart == 0`, the branch is skipped. `SetDecayEpochStart` is never called; the `DecayEpochStartKey` is never written to the KV store.

**Step 3 — `getDecayEpochStart` returns `ok = false`.**

```go
if len(bz) == 0 {
    return 0, false, nil
}
``` [3](#0-2) 

Since the key was never stored, `bz` is empty and `ok = false` is returned.

**Step 4 — `DeflationCalculationFn` returns `baseRate` forever.**

```go
if !ok {
    return baseRate
}
``` [4](#0-3) 

Every block, `ok = false` causes an early return of the undecayed `baseRate`. The exponential decay formula is never reached, regardless of how many blocks pass.

---

### Impact Explanation

With `DecayRate = 0.065` configured but decay never applied, the Mint module mints at `baseRate` indefinitely instead of at `baseRate × (1 - 0.065)^months_elapsed`. The ADR documents the intended formula explicitly:

```
inflation = base_rate × (1 - decay_rate) ^ months_elapsed
``` [5](#0-4) 

Over the chain lifetime this produces unbacked token issuance above the intended supply curve. The `MaxSupply` cap would be breached sooner than the decay model predicts, triggering a chain halt at an unintended time, or — if `MaxSupply` is set to zero (unlimited) — the supply grows without bound at the full base rate.

---

### Likelihood Explanation

The precondition is a genesis file with `DecayRate > 0` and `DecayEpochStart = 0`. This is a plausible misconfiguration: `DecayEpochStart = 0` is the protobuf default for `uint64`, so any genesis file that sets `DecayRate` but omits `decay_epoch_start` will silently produce this state. The validation gives no warning. The integration test config (`inflation.jsonnet`) explicitly sets `decay_epoch_start: '1'`, confirming that `0` is a distinct, meaningful value — but the code treats it as "not set" rather than "genesis height." [6](#0-5) 

---

### Recommendation

Add the inverse check to `GenesisState.Validate()`:

```go
if gs.Params.DecayRate.IsPositive() && gs.DecayEpochStart == 0 {
    return fmt.Errorf("decay_epoch_start must be non-zero when decay_rate is positive")
}
```

Alternatively, treat `DecayEpochStart = 0` as "start decay at genesis height" and store it unconditionally in `InitGenesis` when `DecayRate > 0`.

---

### Proof of Concept

```go
func (s *KeeperSuite) TestDecaySkippedWhenEpochStartIsZero() {
    params := types.DefaultParams()
    params.DecayRate = sdkmath.LegacyNewDecWithPrec(65, 3) // 0.065

    // Genesis with DecayRate > 0 but DecayEpochStart = 0
    gs := types.GenesisState{
        Params:          params,
        DecayEpochStart: 0,
    }
    // Validate passes — no error
    s.Require().NoError(gs.Validate())

    // InitGenesis installs the state
    s.keeper.InitGenesis(s.ctx, gs)

    mintParams := minttypes.DefaultParams()
    minter := minttypes.DefaultInitialMinter()
    bondedRatio := sdkmath.LegacyNewDecWithPrec(50, 2)
    baseRate := minttypes.DefaultInflationCalculationFn(s.ctx, minter, mintParams, bondedRatio)

    // Advance many blocks — decay should reduce inflation below baseRate
    s.ctx = s.ctx.WithBlockHeight(int64(mintParams.BlocksPerYear))
    inflation := s.keeper.DeflationCalculationFn()(s.ctx, minter, mintParams, bondedRatio)

    // FAILS: inflation == baseRate, decay was never applied
    s.Require().True(inflation.LT(baseRate),
        "expected decay to reduce inflation below baseRate, got %s == %s", inflation, baseRate)
}
```

The assertion fails because `inflation == baseRate` — the configured 6.5% monthly decay is silently ignored for the entire chain lifetime.

### Citations

**File:** x/inflation/types/genesis.go (L19-21)
```go
	if !gs.Params.DecayRate.IsPositive() && gs.DecayEpochStart != 0 {
		return fmt.Errorf("decay_epoch_start must be zero when decay is disabled")
	}
```

**File:** x/inflation/keeper/genesis.go (L19-23)
```go
	if genState.DecayEpochStart != 0 {
		if err := k.SetDecayEpochStart(ctx, genState.DecayEpochStart); err != nil {
			panic(err)
		}
	}
```

**File:** x/inflation/keeper/keeper.go (L104-105)
```go
	if len(bz) == 0 {
		return 0, false, nil
```

**File:** x/inflation/keeper/mint.go (L42-44)
```go
		if !ok {
			return baseRate
		}
```

**File:** doc/architecture/adr-007.md (L21-22)
```markdown
  inflation = base_rate × (1 - decay_rate) ^ months_elapsed
  where months_elapsed = (current_height - decay_epoch) / blocks_per_month
```

**File:** integration_tests/configs/inflation.jsonnet (L51-52)
```text
          decay_epoch_start: '1',
        },
```
