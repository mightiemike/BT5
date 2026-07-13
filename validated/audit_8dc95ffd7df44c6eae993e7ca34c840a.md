### Title
Unbounded `ExitDuration` in Tier Governance Proposals Enables Permanent Principal Lock — (`File: x/tieredrewards/types/tier.go`)

### Summary

`Tier.Validate()` enforces a lower bound (`ExitDuration > 0`) but no upper bound on `ExitDuration`. A governance proposal can set `ExitDuration` to an arbitrarily large value (up to `math.MaxInt64` nanoseconds ≈ 292 years). Combined with `CloseOnly = true` (settable in the same `MsgUpdateTier` proposal), this permanently locks all user principal in the affected tier with no recovery path.

### Finding Description

`Tier.Validate()` in `x/tieredrewards/types/tier.go` validates `ExitDuration` only for positivity:

```go
if t.ExitDuration <= 0 {
    return fmt.Errorf("exit duration must be positive")
}
```

No maximum is enforced. By contrast, `BonusApy` is explicitly capped at `1.0` with the comment "to prevent governance from draining the rewards pool." No analogous cap exists for `ExitDuration`.

`ExitDuration` is stored in the tier via `SetTier` (called from `AddTier` and `UpdateTier` governance message handlers) and is consumed at `TriggerExitFromTier` time:

```go
pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)
// → p.ExitUnlockAt = blockTime.Add(duration)
```

`ExitUnlockAt` is then the gate for both exit paths:
- `TierUndelegate` / `ExitTierWithDelegation` require `CompletedExitLockDuration(blockTime)` → `!blockTime.Before(ExitUnlockAt)`
- `ClearPosition` (the only escape hatch) is **blocked** when `CloseOnly = true`

A single `MsgUpdateTier` governance proposal can atomically set both `ExitDuration = math.MaxInt64` and `CloseOnly = true`.

### Impact Explanation

After such a proposal passes:

1. Users who have not yet triggered exit are stuck: they cannot redelegate, add to position, or clear position (all blocked by `CloseOnly`). Their only option is to call `TriggerExitFromTier`, which sets `ExitUnlockAt` ≈ 292 years in the future.
2. Users who trigger exit after the proposal have `ExitUnlockAt` set 292 years ahead. `ClearPosition` is blocked (`CloseOnly`). `TierUndelegate` and `ExitTierWithDelegation` require `CompletedExitLockDuration` → false.
3. All principal in the tier is permanently inaccessible. Users can still claim rewards but can never recover their locked tokens.

The corrupted invariant is: **every position owner must have a reachable path to withdraw their principal**. An unbounded `ExitDuration` combined with `CloseOnly` breaks this invariant irreversibly.

### Likelihood Explanation

The attack requires a governance proposal to pass. On Cosmos SDK chains, governance is a standard production entrypoint reachable by any token holder who meets the deposit threshold. A captured, compromised, or socially-engineered governance majority can pass such a proposal. The risk is elevated because the two parameters (`ExitDuration` and `CloseOnly`) can be set atomically in a single `MsgUpdateTier` message, leaving no window for users to react between the two changes.

### Recommendation

Add an explicit upper bound on `ExitDuration` in `Tier.Validate()`, analogous to the existing `BonusApy` cap:

```go
const MaxExitDuration = 10 * 365 * 24 * time.Hour // e.g. 10 years

if t.ExitDuration > MaxExitDuration {
    return fmt.Errorf("exit duration must not exceed %s: got %s", MaxExitDuration, t.ExitDuration)
}
```

This mirrors the rationale already applied to `BonusApy` and ensures governance cannot set a duration that permanently traps user principal.

### Proof of Concept

1. Governance submits and passes `MsgUpdateTier` with `exit_duration = 9223372036854775807ns` (≈292 years) and `close_only = true` for an active tier.
2. Any position owner in that tier calls `TriggerExitFromTier` → `ExitUnlockAt = blockTime + 292 years` is written to state.
3. Owner calls `ClearPosition` → rejected: `CloseOnly` tier blocks `ClearPosition`.
4. Owner calls `TierUndelegate` → rejected: `CompletedExitLockDuration` is false (292 years remain).
5. Owner calls `ExitTierWithDelegation` → rejected: same gate.
6. Principal is permanently locked with no recovery path.

---

**Root cause:** [1](#0-0) 

**`ExitDuration` stored in tier state, consumed at trigger time:** [2](#0-1) 

**`TriggerExitFromTier` reads live tier `ExitDuration`:** [3](#0-2) 

**`ClearPosition` blocked for `CloseOnly` tiers (ADR):** [4](#0-3) 

**`BonusApy` has an explicit cap (no analogous cap for `ExitDuration`):** [5](#0-4) 

**`SetTier` called from governance message handlers:** [6](#0-5)

### Citations

**File:** x/tieredrewards/types/tier.go (L15-17)
```go
	if t.ExitDuration <= 0 {
		return fmt.Errorf("exit duration must be positive")
	}
```

**File:** x/tieredrewards/types/tier.go (L27-30)
```go
	// Cap BonusApy at 100% to prevent governance from draining the rewards pool.
	if t.BonusApy.GT(math.LegacyOneDec()) {
		return fmt.Errorf("bonus apy must not exceed 1.0 (100%%): got %s", t.BonusApy)
	}
```

**File:** x/tieredrewards/types/position.go (L71-74)
```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
	p.ExitTriggeredAt = blockTime
	p.ExitUnlockAt = blockTime.Add(duration)
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L366-368)
```go
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)

```

**File:** doc/architecture/adr-006.md (L76-76)
```markdown
When a tier is marked `CloseOnly` by governance, these messages are **blocked**: LockTier, CommitDelegation, AddToPosition, TierRedelegate, ClearPosition. **Allowed**: TriggerExit, TierUndelegate, WithdrawFromTier, ClaimTierRewards, ExitTierWithDelegation. This lets governance sunset a tier while existing positions close out.
```

**File:** x/tieredrewards/keeper/msg_server_auth.go (L46-48)
```go
	if err := ms.SetTier(ctx, msg.Tier); err != nil {
		return nil, err
	}
```
