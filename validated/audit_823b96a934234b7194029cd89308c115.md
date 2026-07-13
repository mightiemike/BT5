### Title
Accrued Bonus Rewards Permanently Forfeited When Pool Is Insufficient During `BeforeRedelegationSlashed` Hook — (`x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the error is silently swallowed and the position is immediately persisted with fully-advanced bonus checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`). The user's accrued bonus rewards are permanently destroyed — they can never be retried or recovered — even though the user did nothing wrong. The cause is a validator slash event combined with a temporarily empty rewards pool, both entirely outside the user's control.

---

### Finding Description

The `BeforeRedelegationSlashed` staking hook fires when a redelegation entry on a tier position is about to be slashed. It calls `slashRedelegationPosition`, which in turn calls `processEventsAndClaimBonus` to settle accrued bonus before the slash modifies shares.

Inside `processEventsAndClaimBonus` (`claim_rewards.go`), the following sequence occurs before the pool sufficiency check:

1. The event loop iterates all pending validator events since `pos.LastEventSeq`. For each event, `pos.UpdateLastEventSeq(entry.Seq)` advances the in-memory position's event pointer, and `decrementEventRefCount` writes the decremented reference count (or deletes the event) to persistent state.
2. After the loop, `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` advances `pos.LastBonusAccrual` to the current block time.
3. `pos.UpdateLastKnownBonded(bonded)` updates the bonded-state checkpoint.
4. Only then does `sufficientBonusPoolBalance` run. If the pool is short, it returns `ErrInsufficientBonusPool` — but all three checkpoints have already been mutated in memory, and event reference counts have already been decremented on-chain.

Back in `slashRedelegationPosition`:

```go
// slash.go:54-64
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    // Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt.
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
    } else {
        return err
    }
}
// execution continues with the mutated `pos`
...
return k.setPositionWithState(ctx, pos, ...)
```

The `ErrInsufficientBonusPool` branch does not return; it falls through to `setPositionWithState`, which persists the position with the already-advanced checkpoints. The accrual window that produced the bonus is now permanently behind `LastBonusAccrual`; the event sequence numbers are past `LastEventSeq`; the events themselves may have been garbage-collected. There is no mechanism to replay or recover the forfeited bonus.

By contrast, all **user-driven** paths (`ClaimTierRewards`, `AddToPosition`, `Undelegate`, `Redelegate`, `ClearPosition`) fail atomically on `ErrInsufficientBonusPool` — the transaction rolls back and the user retries after the pool is replenished. The slash hook path has no such safety net.

---

### Impact Explanation

A tier position owner who has a live redelegation loses all accrued bonus rewards for the period since their last claim, with no recourse. The loss is proportional to the position size, the tier's `BonusApy`, and the time elapsed since the last claim. Because the checkpoints are advanced and the events are reference-count-decremented, the lost amount is irrecoverable even after the pool is later replenished. The corrupted value is the `bonus_rewards` balance that should have been transferred from `RewardsPoolName` to the owner's account.

**Impact: High**

---

### Likelihood Explanation

The trigger requires two simultaneous conditions: (a) a validator that hosts at least one tier-position redelegation is slashed, and (b) the `RewardsPoolName` module account balance is insufficient to cover the accrued bonus at that moment. Validator slashes are infrequent but are a normal, expected protocol event. The rewards pool can be transiently empty if governance has not yet funded it, if a large prior claim drained it, or if the `BeginBlocker` top-up has not yet run in the same block. Neither condition requires any action by the affected user.

**Likelihood: Low**

---

### Recommendation

Move the `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` calls to after the successful `SendCoinsFromModuleToAccount`, so that checkpoints are only advanced when the bonus is actually paid. In `slashRedelegationPosition`, if `ErrInsufficientBonusPool` is returned, do not advance the position's checkpoints — either return the error (accepting the chain-halt risk for this edge case) or skip the checkpoint update so the user can retry the bonus claim later via a user-driven path once the pool is replenished.

---

### Proof of Concept

**Entry path:**

1. Alice creates a tier position delegating to `valA`, then redelegates to `valB` via `MsgTierRedelegate`. A `RedelegationMapping(unbondingId → positionId)` entry is created.
2. Time passes; Alice accrues significant bonus rewards (e.g., 30 days × `BonusApy`).
3. The rewards pool is empty (governance has not yet funded it, or a prior large claim drained it).
4. `valA` (the source validator) is slashed. The SDK calls `SlashRedelegation`, which fires `BeforeRedelegationSlashed(unbondingId, sharesToUnbond)`.
5. `slashRedelegationPosition` is called. `processEventsAndClaimBonus` runs:
   - Iterates all events since `pos.LastEventSeq`, decrementing reference counts on-chain.
   - Advances `pos.LastBonusAccrual` to `blockTime` and `pos.LastKnownBonded`.
   - Calls `sufficientBonusPoolBalance` → fails with `ErrInsufficientBonusPool`.
6. The error is swallowed. `setPositionWithState` persists the position with the advanced checkpoints.
7. Alice later calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` finds no events since the now-advanced `LastEventSeq` and computes zero bonus for the segment `[LastBonusAccrual, now]` (which starts at the slash block, not at Alice's original accrual start). Alice receives zero bonus for the entire pre-slash period.

**Relevant code locations:**

- Checkpoint advancement before pool check: [1](#0-0) 
- Pool sufficiency check (too late): [2](#0-1) 
- Error swallowed, position persisted with advanced checkpoints: [3](#0-2) 
- Hook entry point: [4](#0-3) 
- `decrementEventRefCount` (state write inside the loop, before pool check): [5](#0-4) 
- `applyBonusAccrualCheckpoint` (advances `LastBonusAccrual` before pool check): [6](#0-5)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L192-220)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L54-77)
```go
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

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```

**File:** x/tieredrewards/keeper/validator_events.go (L85-101)
```go
func (k Keeper) decrementEventRefCount(ctx context.Context, valAddr sdk.ValAddress, seq uint64) error {
	key := collections.Join(valAddr, seq)
	event, err := k.ValidatorEvents.Get(ctx, key)
	if errors.Is(err, collections.ErrNotFound) {
		return nil // already cleaned up
	}
	if err != nil {
		return err
	}

	if event.ReferenceCount <= 1 {
		return k.ValidatorEvents.Remove(ctx, key)
	}

	event.ReferenceCount--
	return k.ValidatorEvents.Set(ctx, key, event)
}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L15-21)
```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
	accrualEnd := blockTime
	if pos.CompletedExitLockDuration(blockTime) {
		accrualEnd = pos.ExitUnlockAt
	}
	pos.UpdateLastBonusAccrual(accrualEnd)
}
```
