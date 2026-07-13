### Title
Bonus Reward Checkpoints Advance Without Payment When Pool Is Insufficient During Redelegation Slash - (`x/tieredrewards/keeper/slash.go`)

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the function silently swallows the error to avoid a chain halt. However, because `processEventsAndClaimBonus` modifies the position's bonus checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) via a pointer **before** the pool balance check, and also decrements event reference counts in the store, the position's state is advanced as if the bonus was paid — even though no coins were transferred. The bonus for that period is permanently lost.

### Finding Description

`processEventsAndClaimBonus` takes `*types.PositionState` and mutates it in-place throughout its execution:

```go
// x/tieredrewards/keeper/claim_rewards.go
for _, entry := range events {
    // ...
    pos.UpdateLastEventSeq(entry.Seq)          // mutates *pos
    if err := k.decrementEventRefCount(...); err != nil { ... }
}
applyBonusAccrualCheckpoint(&pos.Position, blockTime)  // mutates *pos
pos.UpdateLastKnownBonded(bonded)                       // mutates *pos

if totalBonus.IsZero() { return sdk.NewCoins(), nil }

bonusCoins := sdk.NewCoins(...)
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err   // ← error returned AFTER all mutations
}
// send coins only if we reach here
```

By the time `ErrInsufficientBonusPool` is returned, all three checkpoint fields on `*pos` have already been updated, and all event reference counts have been decremented in the KV store.

In `slashRedelegationPosition`, this error is caught and swallowed:

```go
// x/tieredrewards/keeper/slash.go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // silently continues — pos is already mutated
    } else {
        return err
    }
}
// pos.LastEventSeq, LastBonusAccrual, LastKnownBonded are all advanced
// but no bonus was paid
fullSlash := sharesToUnbond.GTE(pos.Delegation.Shares)
if fullSlash {
    pos.Delegation = nil
    pos.ClearBonusCheckpoints()  // resets LastBonusAccrual/LastKnownBonded, NOT LastEventSeq
    return k.setPositionWithState(ctx, pos, ...)
}
pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
return k.setPositionWithState(ctx, pos, nil)  // saves advanced checkpoints
```

For a **partial slash**: the position is saved with `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded` all advanced to the slash block time, as if the bonus was fully paid. The position will never re-process those events; the bonus is gone.

For a **full slash**: `ClearBonusCheckpoints` resets `LastBonusAccrual` and `LastKnownBonded` but does **not** reset `LastEventSeq`. The position's delegation is nil so it cannot claim bonus anyway. The bonus is gone.

In both cases, the event reference counts were decremented in the store. The invariant `sum(bonus_accrued_by_positions) = sum(bonus_paid_from_pool)` is broken: the accrual side advanced, the payment side did not.

### Impact Explanation

- **Permanent loss of bonus rewards**: The position owner is entitled to bonus for the period between `LastBonusAccrual` and the slash block. That bonus is computed, the checkpoints advance, but no coins are transferred. The owner cannot recover this bonus on any subsequent claim because `LastEventSeq` and `LastBonusAccrual` have already moved past the relevant period.
- **Stuck funds in the rewards pool**: The bonus that should have been paid remains in the `RewardsPoolName` module account indefinitely. It will be diluted among future claimants rather than going to the rightful owner.
- **Silent failure**: No error is surfaced to the user or the chain. The position appears to have been processed correctly.

### Likelihood Explanation

The trigger requires two concurrent conditions:
1. A tier position is in an active redelegation (after `MsgTierRedelegate`).
2. The destination validator is slashed during the redelegation period while the bonus pool is empty or insufficient.

The bonus pool can be drained by normal user claims (`MsgClaimTierRewards`, `MsgTierUndelegate`, etc.). Validator slashing during redelegation is a normal chain event. Both conditions are independently reachable by unprivileged actors (any user can lock and redelegate; slashing is triggered by consensus misbehavior). The combination is realistic in a live network with active staking.

### Recommendation

In `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, the position's in-memory checkpoint mutations must be rolled back before saving the position. Specifically, restore `pos.LastEventSeq`, `pos.LastBonusAccrual`, and `pos.LastKnownBonded` to their pre-call values, and do not decrement event reference counts for events that were not paid. One approach is to snapshot the position state before calling `processEventsAndClaimBonus` and restore it on `ErrInsufficientBonusPool`:

```go
posSnapshot := pos  // copy before call
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        pos = posSnapshot  // restore checkpoints
        // Note: reference count decrements in the store also need to be undone,
        // or processEventsAndClaimBonus must be refactored to check pool balance first.
    } else {
        return err
    }
}
```

A cleaner fix is to check pool sufficiency **before** mutating any state or decrementing reference counts inside `processEventsAndClaimBonus`.

### Proof of Concept

1. User A calls `MsgLockTier` → position P created, delegated to validator V1.
2. User A calls `MsgTierRedelegate` → position P redelegated to V2; `RedelegationMappings[unbondingId] = P.Id` is set.
3. Bonus pool is drained to zero by other users calling `MsgClaimTierRewards`.
4. V2 is slashed during the redelegation period → `BeforeRedelegationSlashed(unbondingId, sharesToUnbond)` fires.
5. `slashRedelegationPosition` calls `processEventsAndClaimBonus(&pos)`.
6. Inside `processEventsAndClaimBonus`: `pos.LastEventSeq` advances, `pos.LastBonusAccrual` advances, event ref counts decremented. Pool check fails → `ErrInsufficientBonusPool` returned.
7. Error swallowed. `pos` (with advanced checkpoints) is saved via `setPositionWithState`.
8. User A later calls `MsgClaimTierRewards` for position P. `processEventsAndClaimBonus` finds no new events (LastEventSeq is already at the latest), computes zero bonus for the already-advanced `LastBonusAccrual`. The bonus for the redelegation period is permanently unclaimable. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-217)
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-232)
```go
	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```
