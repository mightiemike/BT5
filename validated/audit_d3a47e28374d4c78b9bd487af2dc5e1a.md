### Title
Unbounded `ExitDuration` in `Tier.Validate()` Allows Governance to Permanently Lock User Positions - (`File: x/tieredrewards/types/tier.go`)

### Summary
`Tier.Validate()` enforces only a lower bound (`ExitDuration > 0`) on the exit lock duration but imposes no upper bound. A governance proposal setting `ExitDuration` to an astronomically large value (e.g., `math.MaxInt64` nanoseconds ≈ 292 years) is accepted without error. Any user who subsequently calls `MsgTriggerExitFromTier` — or who locks with `trigger_exit_immediately = true` — will have `ExitUnlockAt` set centuries into the future, making `MsgTierUndelegate` and `MsgWithdrawFromTier` permanently unreachable. If the tier is simultaneously marked `CloseOnly`, `MsgClearPosition` is also blocked, leaving the position's staked principal with no exit path.

---

### Finding Description

`Tier.Validate()` checks only that `ExitDuration` is strictly positive:

```go
if t.ExitDuration <= 0 {
    return fmt.Errorf("exit duration must be positive")
}
```

No maximum is enforced. [1](#0-0) 

`MsgAddTier` and `MsgUpdateTier` both call `ms.SetTier(ctx, msg.Tier)` directly without any additional duration bounds check in the message server:

```go
func (ms msgServer) AddTier(...) {
    ...
    if err := ms.SetTier(ctx, msg.Tier); err != nil { ... }
}
func (ms msgServer) UpdateTier(...) {
    ...
    if err := ms.SetTier(ctx, msg.Tier); err != nil { ... }
}
``` [2](#0-1) 

When a user calls `MsgTriggerExitFromTier`, the keeper reads the tier's `ExitDuration` and applies it directly to the position:

```go
pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)
``` [3](#0-2) 

`TriggerExit` sets `ExitUnlockAt = blockTime + duration` with no overflow or sanity check:

```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
    p.ExitTriggeredAt = blockTime
    p.ExitUnlockAt = blockTime.Add(duration)
}
``` [4](#0-3) 

`MsgTierUndelegate` and `MsgWithdrawFromTier` both require `CompletedExitLockDuration` to be true before proceeding. With `ExitUnlockAt` set to year 2316, neither message can ever succeed. [5](#0-4) 

The only escape valve is `MsgClearPosition`, but it is explicitly blocked when the tier is `CloseOnly`:

> **MsgClearPosition**: Tier not close-only [6](#0-5) 

A single governance proposal can set both `ExitDuration = math.MaxInt64` and `CloseOnly = true` simultaneously, since both fields are part of the same `Tier` struct passed in `MsgUpdateTier`. [7](#0-6) 

The same lock applies to positions created with `trigger_exit_immediately = true` in `MsgLockTier` or `MsgCommitDelegationToTier`, since the exit clock is started at creation time using the same `tier.ExitDuration`. [8](#0-7) 

---

### Impact Explanation

**High.** The staked principal held in the per-position delegator account (`DelegatorAddress`) cannot be withdrawn or transferred. `MsgTierUndelegate`, `MsgWithdrawFromTier`, and `MsgExitTierWithDelegation` all gate on `ExitUnlockAt` having passed. With `ExitUnlockAt` set centuries in the future and `ClearPosition` blocked by `CloseOnly`, the position's funds are permanently inaccessible. The corrupted on-chain value is `Position.ExitUnlockAt`, which controls the sole unlock gate for the locked principal.

---

### Likelihood Explanation

**Low.** Exploitation requires a governance proposal to pass with an extreme `ExitDuration` value — either through a misconfiguration error (e.g., confusing nanoseconds with seconds, submitting `315360000000000000` instead of `315360000000000`) or a malicious governance attack. This matches the external report's characterization of "low likelihood, requires a configuration error from the admin."

---

### Recommendation

Add a maximum cap on `ExitDuration` inside `Tier.Validate()`, for example:

```go
const MaxExitDuration = 5 * 365 * 24 * time.Hour // 5 years

if t.ExitDuration > MaxExitDuration {
    return fmt.Errorf("exit duration must not exceed %s: got %s", MaxExitDuration, t.ExitDuration)
}
```

This mirrors the external report's recommendation to enforce a maximum duration (e.g., 2 years) on the `_duration` parameter in `createSchedule`. [9](#0-8) 

---

### Proof of Concept

1. Governance submits and passes `MsgUpdateTier` with:
   ```json
   {
     "id": 1,
     "exit_duration": "9223372036854775807ns",
     "bonus_apy": "0.04",
     "min_lock_amount": "1000000",
     "close_only": true
   }
   ```
   This passes `Tier.Validate()` because `ExitDuration > 0`. [10](#0-9) 

2. A user calls `MsgLockTier` with `trigger_exit_immediately = true` (or calls `MsgTriggerExitFromTier` on an existing position). The keeper executes `pos.TriggerExit(blockTime, tier.ExitDuration)`, setting `ExitUnlockAt` to approximately year 2316. [11](#0-10) 

3. The user attempts `MsgTierUndelegate` — rejected: exit lock duration not reached (`ErrExitLockDurationNotReached`). [12](#0-11) 

4. The user attempts `MsgClearPosition` — rejected: tier is `CloseOnly`. [6](#0-5) 

5. The user's staked principal is permanently locked with no reachable exit path.

### Citations

**File:** x/tieredrewards/types/tier.go (L10-41)
```go
func (t Tier) Validate() error {
	if t.Id == 0 {
		return ErrInvalidTierID
	}

	if t.ExitDuration <= 0 {
		return fmt.Errorf("exit duration must be positive")
	}

	if t.BonusApy.IsNil() {
		return fmt.Errorf("bonus apy cannot be nil")
	}

	if t.BonusApy.IsNegative() {
		return fmt.Errorf("bonus apy cannot be negative: %s", t.BonusApy)
	}

	// Cap BonusApy at 100% to prevent governance from draining the rewards pool.
	if t.BonusApy.GT(math.LegacyOneDec()) {
		return fmt.Errorf("bonus apy must not exceed 1.0 (100%%): got %s", t.BonusApy)
	}

	if t.MinLockAmount.IsNil() {
		return fmt.Errorf("min lock amount cannot be nil")
	}

	if t.MinLockAmount.IsNegative() {
		return fmt.Errorf("min lock amount cannot be negative: %s", t.MinLockAmount)
	}

	return nil
}
```

**File:** x/tieredrewards/keeper/msg_server_auth.go (L33-82)
```go
func (ms msgServer) AddTier(ctx context.Context, msg *types.MsgAddTier) (*types.MsgAddTierResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	has, err := ms.hasTier(ctx, msg.Tier.Id)
	if err != nil {
		return nil, err
	}
	if has {
		return nil, types.ErrTierAlreadyExists
	}

	if err := ms.SetTier(ctx, msg.Tier); err != nil {
		return nil, err
	}

	if err := ms.emitTierChangedEvent(ctx, types.TierChangeAction_TIER_CHANGE_ACTION_NEW, msg.Tier); err != nil {
		return nil, err
	}

	return &types.MsgAddTierResponse{}, nil
}

func (ms msgServer) UpdateTier(ctx context.Context, msg *types.MsgUpdateTier) (*types.MsgUpdateTierResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	oldTier, err := ms.getTier(ctx, msg.Tier.Id)
	if err != nil {
		return nil, err
	}

	if !oldTier.BonusApy.Equal(msg.Tier.BonusApy) {
		if err := ms.claimRewardsAndUpdateTierPositions(ctx, msg.Tier.Id); err != nil {
			return nil, err
		}
	}

	if err := ms.SetTier(ctx, msg.Tier); err != nil {
		return nil, err
	}

	if err := ms.emitTierChangedEvent(ctx, types.TierChangeAction_TIER_CHANGE_ACTION_UPDATE, msg.Tier); err != nil {
		return nil, err
	}

	return &types.MsgUpdateTierResponse{}, nil
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L361-368)
```go
	tier, err := ms.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)

```

**File:** x/tieredrewards/types/position.go (L61-63)
```go
func (p Position) CompletedExitLockDuration(blockTime time.Time) bool {
	return !p.ExitUnlockAt.IsZero() && !blockTime.Before(p.ExitUnlockAt)
}
```

**File:** x/tieredrewards/types/position.go (L71-74)
```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
	p.ExitTriggeredAt = blockTime
	p.ExitUnlockAt = blockTime.Add(duration)
}
```

**File:** doc/architecture/adr-006.md (L163-163)
```markdown
| **MsgClearPosition** | Cancel exit. Settles rewards first. If delegated, resets `LastBonusAccrual` to block_time. No-op if not exiting. | Tier not close-only; if exit elapsed: must be delegated and not unbonding |
```

**File:** doc/architecture/adr-006.md (L181-181)
```markdown
-> If trigger_exit_immediately: set ExitTriggeredAt, ExitUnlockAt
```

**File:** x/tieredrewards/types/errors.go (L19-19)
```go
	ErrExitLockDurationNotReached       = errors.Register(ModuleName, 14, "exit lock duration not reached")
```
