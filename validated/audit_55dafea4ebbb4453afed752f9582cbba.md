### Title
Bonus Rewards Permanently Forfeited When `BeforeRedelegationSlashed` Fires With Insufficient Pool — (File: `x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is deliberately swallowed to prevent chain halt. However, `processEventsAndClaimBonus` advances the position's bonus checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) **before** the pool sufficiency check. The mutated position is then persisted via `setPositionWithState`. This permanently forfeits the accrued bonus rewards for the position owner — even after the pool is later replenished — because the checkpoints have already been advanced past the accrual period.

---

### Finding Description

`processEventsAndClaimBonus` performs two categories of mutations before reaching the pool check:

**1. In-memory mutations on `pos` (pointer receiver):**
- `pos.UpdateLastEventSeq(entry.Seq)` — inside the event loop
- `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` — advances `LastBonusAccrual`
- `pos.UpdateLastKnownBonded(bonded)` — updates bonded state

**2. Store writes:**
- `k.decrementEventRefCount(ctx, valAddr, entry.Seq)` — inside the event loop, before the pool check

The pool check occurs **after** all of the above:

```go
// x/tieredrewards/keeper/claim_rewards.go lines 215-231
applyBonusAccrualCheckpoint(&pos.Position, blockTime)
pos.UpdateLastKnownBonded(bonded)

if totalBonus.IsZero() {
    return sdk.NewCoins(), nil
}
// ...
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err   // <-- checkpoints already advanced, store already mutated
}
``` [1](#0-0) 

In `slashRedelegationPosition`, the `ErrInsufficientBonusPool` error is swallowed:

```go
// x/tieredrewards/keeper/slash.go lines 54-64
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // error swallowed — pos has advanced checkpoints, store has decremented ref counts
    } else {
        return err
    }
}
``` [2](#0-1) 

For a **partial slash**, execution continues and the position is persisted with the advanced checkpoints but no bonus paid:

```go
// x/tieredrewards/keeper/slash.go lines 76-77
pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
return k.setPositionWithState(ctx, pos, nil)
``` [3](#0-2) 

The position remains delegated with `LastBonusAccrual` advanced to the current block time and `LastEventSeq` advanced past all pending events. Any subsequent call to `processEventsAndClaimBonus` for this position will compute zero bonus for the forfeited period, even after the pool is replenished.

This is structurally analogous to the external report: the accounting state (checkpoints) is advanced as if payment occurred, but the conditional logic (pool check) silently skips the actual payment, creating a permanent mismatch between what was owed and what was paid.

---

### Impact Explanation

**Impact: Medium.** Position owners whose redelegation entry is partially slashed while the bonus pool is insufficient permanently lose all bonus rewards that accrued from `LastBonusAccrual` up to the slash block time. These are real tokens from the rewards pool that the owner is entitled to. The principal (locked tokens) is not at risk, but the bonus rewards are irrecoverably forfeited — unlike user-driven paths, which fail atomically and allow retry after pool replenishment.

The contrast with user-driven paths is explicit in the ADR:
> "User-driven paths (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished." [4](#0-3) 

In the hook path, no such retry is possible because the checkpoints are advanced and persisted.

---

### Likelihood Explanation

**Likelihood: Medium.** The trigger requires two concurrent conditions:

1. **Redelegation slash**: A user must have an active `MsgTierRedelegate` redelegation entry that is within the SDK's redelegation slash window (creation height ≥ infraction height). This is a normal validator lifecycle event (downtime or double-sign).

2. **Insufficient bonus pool**: The `RewardsPoolName` module account must have insufficient balance to cover the accrued bonus. The pool is continuously drained by the BeginBlocker top-up mechanism and by user claims. A large number of positions or a long accrual period without pool replenishment can deplete it.

Neither condition requires privileged access. Any delegator who uses `MsgTierRedelegate` is exposed if their destination validator is subsequently slashed while the pool is low.

---

### Recommendation

The root cause is that `processEventsAndClaimBonus` advances checkpoints and decrements event reference counts before the pool sufficiency check. Two remediation options:

1. **Move the pool check before any state mutation**: Check `sufficientBonusPoolBalance` before the event loop and before advancing any checkpoints. If the pool is insufficient, return early without mutating `pos` or decrementing ref counts.

2. **Use a branched context in the hook path**: In `slashRedelegationPosition`, wrap the `processEventsAndClaimBonus` call in a `CacheContext`. If `ErrInsufficientBonusPool` is returned, discard the cache (rolling back ref count decrements) and do not persist the advanced checkpoints. The position is then saved with its original checkpoints, allowing the owner to claim the bonus later after the pool is replenished.

Option 2 is consistent with the existing design intent (avoid chain halt) while eliminating the permanent forfeiture.

---

### Proof of Concept

1. User calls `MsgTierRedelegate` to move their position from validator A to validator B. A `RedelegationMapping` entry is created.
2. The bonus pool is depleted by concurrent claims or BeginBlocker top-ups.
3. Validator B commits a downtime infraction. The SDK's `SlashRedelegation` fires.
4. `BeforeRedelegationSlashed` → `slashRedelegationPosition` is called with the position's `unbondingId`.
5. `processEventsAndClaimBonus` runs: advances `LastBonusAccrual` to the current block time, advances `LastEventSeq` past all pending events, decrements event ref counts — then returns `ErrInsufficientBonusPool` because the pool cannot cover the accrued bonus.
6. The error is swallowed. `pos` now has advanced checkpoints but no bonus was paid.
7. `setPositionWithState(ctx, pos, nil)` persists the position with the advanced checkpoints.
8. The pool is later replenished by governance.
9. The user calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` computes zero bonus because `LastBonusAccrual` already equals the current block time and there are no unprocessed events. The accrued bonus is permanently lost. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-232)
```go
func (k Keeper) processEventsAndClaimBonus(ctx context.Context, pos *types.PositionState) (sdk.Coins, error) {
	// Rewards should have been claimed before undelegation
	if !pos.IsDelegated() {
		return sdk.NewCoins(), nil
	}

	valAddr, err := sdk.ValAddressFromBech32(pos.Delegation.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	blockTime := sdkCtx.BlockTime()

	totalBonus := math.ZeroInt()
	// Use the persisted bonded state from the last replay, not a hardcoded default.
	// This prevents overpaying bonus for unbonded gaps between claims.
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual

	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	for _, entry := range events {
		evt := entry.Event

		if bonded {
			// Compute bonus for the bonded segment [segmentStart, eventTime]
			// using the snapshot rate at the event.
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
		}

		// Update bonded state based on event type.
		switch evt.EventType {
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND:
			bonded = false
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND:
			bonded = true
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_SLASH:
			// Slash doesn't change bonded state.
		}

		segmentStart = evt.Timestamp
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
	}

	val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return nil, err
	}
	// Defensive: validator bond status check
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}

	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)

	if totalBonus.IsZero() {
		return sdk.NewCoins(), nil
	}

	bondDenom, err := k.stakingKeeper.BondDenom(ctx)
	if err != nil {
		return nil, err
	}

	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L19-77)
```go
func (k Keeper) slashRedelegationPosition(ctx context.Context, unbondingId uint64, sharesToUnbond math.LegacyDec) error {
	positionId, err := k.getRedelegationMapping(ctx, unbondingId)
	if errors.Is(err, collections.ErrNotFound) {
		return nil
	}
	if err != nil {
		return err
	}

	pos, err := k.getPositionState(ctx, positionId)
	if errors.Is(err, types.ErrPositionNotFound) {
		k.logger(ctx).Error("position not found during redelegation slash",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"error", err.Error(),
		)
		return nil
	}
	if err != nil {
		return err
	}

	if !pos.IsDelegated() {
		// Defensive
		k.logger(ctx).Error("delegation missing during BeforeRedelegationSlashed",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"shares_to_unbond", sharesToUnbond.String(),
		)
		return nil
	}

	dstValStr := pos.Delegation.ValidatorAddress

	// Settle bonus against PRE-slash shares.
	if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
		// Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt.
		if errors.Is(err, types.ErrInsufficientBonusPool) {
			k.logger(ctx).Error("insufficient bonus pool during redelegation slash",
				"position_id", pos.Id,
				"error", err.Error(),
			)
		} else {
			return err
		}
	}

	fullSlash := sharesToUnbond.GTE(pos.Delegation.Shares)

	if fullSlash {
		pos.Delegation = nil
		pos.ClearBonusCheckpoints()
		return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
	}
	// In-memory only: the persisted Position carries no share count, and the
	// live delegation will reflect the post-Unbond shares on the next read.
	// Update the local copy so any follow-up logic in this call sees consistent state.
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L48-61)
```go
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
	if bonus.IsZero() {
		return nil
	}

	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetAllBalances(ctx, poolAddr)
	if !poolBalance.IsAllGTE(bonus) {
		return errorsmod.Wrapf(types.ErrInsufficientBonusPool,
			"bonus: %s, pool balance: %s",
			bonus.String(), poolBalance.String())
	}

	return nil
```
