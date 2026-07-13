### Title
Accrued Bonus Rewards Permanently Lost When Bonus Pool Is Empty During Redelegation Slash — (`x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the code silently continues and persists the position with fully advanced bonus checkpoints and already-decremented event reference counts. Because the checkpoints are advanced past the events and the events may be garbage-collected, the user can never reclaim the accrued bonus rewards. The rewards are permanently forfeited — a stricter version of the original "locked rewards" class.

---

### Finding Description

`slashRedelegationPosition` in `x/tieredrewards/keeper/slash.go` is invoked from the `BeforeRedelegationSlashed` staking hook whenever a redelegation entry is slashed. It calls `processEventsAndClaimBonus` to settle bonus rewards against pre-slash shares.

Inside `processEventsAndClaimBonus` (`x/tieredrewards/keeper/claim_rewards.go`), the following operations happen **before** the pool-balance check:

1. **Event reference counts are decremented in the store** — for every event in the loop, `decrementEventRefCount` is called and committed to the live context. If the ref count reaches zero, the event is garbage-collected.
2. **`pos.LastEventSeq` is advanced** — `pos.UpdateLastEventSeq(entry.Seq)` is called inside the loop.
3. **`pos.LastBonusAccrual` is advanced** — `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` is called after the loop.
4. **`pos.LastKnownBonded` is updated** — `pos.UpdateLastKnownBonded(bonded)` is called after the loop.

Only **after** all of the above does the function check the pool:

```go
// x/tieredrewards/keeper/claim_rewards.go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err   // returns error; pos already mutated, ref counts already decremented
}
```

Back in `slashRedelegationPosition`, the `ErrInsufficientBonusPool` error is caught and execution continues:

```go
// x/tieredrewards/keeper/slash.go L54-64
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // silently continues — pos already has advanced checkpoints
    } else {
        return err
    }
}
```

For a **partial slash**, the mutated `pos` (with advanced checkpoints, no bonus paid) is then persisted:

```go
// x/tieredrewards/keeper/slash.go L76-77
pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
return k.setPositionWithState(ctx, pos, nil)
```

For a **full slash**, `ClearBonusCheckpoints()` resets the in-memory checkpoints, but the event reference counts in the store have already been decremented and the events may already be garbage-collected:

```go
// x/tieredrewards/keeper/slash.go L68-71
pos.Delegation = nil
pos.ClearBonusCheckpoints()
return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
```

In both cases the bonus rewards accrued up to the slash block are permanently unrecoverable.

---

### Impact Explanation

The corrupted value is the user's accrued bonus rewards for the period `[pos.LastBonusAccrual, slashBlockTime]`. After the hook completes:

- `pos.LastBonusAccrual` is advanced to `slashBlockTime` (or zeroed on full slash).
- `pos.LastEventSeq` is advanced past all processed events.
- Event reference counts are decremented; events with zero references are deleted from the store.
- No `SendCoinsFromModuleToAccount` was executed.

The user has no path to recover the forfeited bonus. `MsgClaimTierRewards` will replay from the new `LastEventSeq` and `LastBonusAccrual`, skipping the lost segment entirely.

---

### Likelihood Explanation

The trigger requires two concurrent conditions:

1. A tier position is in the redelegation period (21-day staking unbonding window after `MsgTierRedelegate`).
2. The destination validator is slashed while the bonus pool balance is zero or below the accrued bonus amount.

Both conditions are realistic in production: validators are slashed for downtime or double-signing; the bonus pool can be depleted by concurrent reward claims. Neither condition requires privileged access. Any delegator who redelegates a tier position is exposed for the full 21-day redelegation window.

---

### Recommendation

Move the `sufficientBonusPoolBalance` check to the **top** of `processEventsAndClaimBonus`, before the event-replay loop, so that no checkpoints are advanced and no reference counts are decremented when the pool is insufficient. Alternatively, use a cache context inside `processEventsAndClaimBonus` and only commit it after the `SendCoinsFromModuleToAccount` succeeds, so that a pool-insufficient failure leaves the store and the `pos` object completely unchanged.

---

### Proof of Concept

1. User calls `MsgTierRedelegate` — position enters the 21-day redelegation window; a `RedelegationMappings[unbondingId → positionId]` entry is created.
2. Time passes; the position accrues bonus rewards (non-zero `totalBonus` computed in `processEventsAndClaimBonus`).
3. The bonus pool is drained to zero by other users claiming rewards.
4. The destination validator is slashed; the staking module calls `BeforeRedelegationSlashed(unbondingId, sharesToUnbond)`.
5. `slashRedelegationPosition` is invoked; it calls `processEventsAndClaimBonus(ctx, &pos)`.
6. Inside `processEventsAndClaimBonus`: the event loop runs, `decrementEventRefCount` is called for each event (store writes committed), `pos.LastEventSeq` / `pos.LastBonusAccrual` / `pos.LastKnownBonded` are all advanced in-memory.
7. `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`; the function returns without paying bonus.
8. `slashRedelegationPosition` catches the error, logs it, and calls `setPositionWithState` with the mutated `pos`.
9. The slash transaction succeeds; all store writes (decremented ref counts, advanced position checkpoints) are committed.
10. The user's accrued bonus for the entire pre-slash period is permanently lost; subsequent `MsgClaimTierRewards` calls start from the new checkpoint and cannot recover it.

**Exact corrupted value**: `bonus = shares × tokensPerShare × bonusApy × duration / SecondsPerYear` for the segment `[pos.LastBonusAccrual, slashBlockTime]`, permanently unclaimable. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** x/tieredrewards/keeper/slash.go (L53-64)
```go
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
```

**File:** x/tieredrewards/keeper/slash.go (L66-77)
```go
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-199)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-232)
```go
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
