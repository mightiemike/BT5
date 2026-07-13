### Title
Missing Upper Bound on `ExitDuration` in `x/tieredrewards` Tier Validation — (File: `x/tieredrewards/types/tier.go`)

### Summary

The `x/tieredrewards` module allows governance to create or update tiers via `MsgAddTier` / `MsgUpdateTier`. The `Tier.Validate()` function enforces a lower bound (`ExitDuration > 0`) but imposes **no upper bound** on `ExitDuration`. Governance can therefore set an arbitrarily large exit commitment period (e.g., 100 years), permanently preventing any user who triggers exit from undelegating or withdrawing their staked tokens.

### Finding Description

`Tier.Validate()` in `x/tieredrewards/types/tier.go` checks:

```go
if t.ExitDuration <= 0 {
    return fmt.Errorf("exit duration must be positive")
}
``` [1](#0-0) 

There is no corresponding upper-bound check. By contrast, `BonusApy` is explicitly capped at 1.0 to prevent pool drainage:

```go
if t.BonusApy.GT(math.LegacyOneDec()) {
    return fmt.Errorf("bonus apy must not exceed 1.0 (100%%): got %s", t.BonusApy)
}
``` [2](#0-1) 

`SetTier` — called by both `AddTier` and `UpdateTier` in the message server — delegates all validation to `tier.Validate()`:

```go
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
    if err := tier.Validate(); err != nil {
        return err
    }
    ...
}
``` [3](#0-2) 

The governance message handlers `AddTier` and `UpdateTier` both call `SetTier` with no additional duration check: [4](#0-3) [5](#0-4) 

When a user calls `MsgTriggerExitFromTier`, the position's `ExitUnlockAt` is set to `block_time + tier.ExitDuration`:

```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
    p.ExitTriggeredAt = blockTime
    p.ExitUnlockAt = blockTime.Add(duration)
}
``` [6](#0-5) 

Both exit paths — `MsgTierUndelegate` and `MsgExitTierWithDelegation` — require `block_time >= ExitUnlockAt` before they execute. With an unbounded `ExitDuration`, `ExitUnlockAt` can be set centuries into the future, making both paths permanently unreachable. [7](#0-6) 

### Impact Explanation

The corrupted invariant is the **position's `ExitUnlockAt` timestamp**, which determines when a user's staked tokens can be recovered. If governance sets `ExitDuration` to an arbitrarily large value (e.g., `math.MaxInt64` nanoseconds ≈ 292 years), any user who calls `MsgTriggerExitFromTier` will have their delegation locked in the tier module's delegator account for that duration. `MsgClearPosition` can cancel the exit, but it only re-locks the position — it does not allow token withdrawal. The user's staked principal is permanently inaccessible.

### Likelihood Explanation

The entry path is a standard on-chain governance proposal (`MsgAddTier` / `MsgUpdateTier`) routed through the `x/gov` module. No private key leak or off-chain action is required. A governance attack (e.g., a validator cartel or a flash-loan-style governance takeover) is a realistic threat model for Cosmos SDK chains. The absence of any upper-bound guard means a single passing proposal is sufficient to lock all existing and future positions in the affected tier.

### Recommendation

Add a maximum `ExitDuration` constant (e.g., `MaxExitDuration = 10 * 365 * 24 * time.Hour` for 10 years) and enforce it in `Tier.Validate()`, mirroring the existing `BonusApy` cap:

```go
const MaxExitDuration = 10 * 365 * 24 * time.Hour

if t.ExitDuration > MaxExitDuration {
    return fmt.Errorf("exit duration must not exceed %s: got %s", MaxExitDuration, t.ExitDuration)
}
```

### Proof of Concept

1. Governance submits `MsgAddTier` (or `MsgUpdateTier`) with `ExitDuration = math.MaxInt64` (≈ 292 years).
2. `Tier.Validate()` passes — only `ExitDuration > 0` is checked.
3. `SetTier` stores the tier with the unbounded duration.
4. A user calls `MsgLockTier` on the affected tier, then `MsgTriggerExitFromTier`.
5. `Position.TriggerExit` sets `ExitUnlockAt = block_time + 292 years`.
6. The user attempts `MsgTierUndelegate` or `MsgExitTierWithDelegation` — both fail because `block_time < ExitUnlockAt`.
7. The user's staked tokens remain locked in the tier module's delegator account indefinitely. [8](#0-7) [3](#0-2) [9](#0-8)

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

**File:** x/tieredrewards/keeper/tier.go (L27-34)
```go
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
	if err := tier.Validate(); err != nil {
		return err
	}
	if err := k.Tiers.Set(ctx, tier.Id, tier); err != nil {
		return errorsmod.Wrapf(err, "%s (tier id %d)", types.ErrTierStore.Error(), tier.Id)
	}
	return nil
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
