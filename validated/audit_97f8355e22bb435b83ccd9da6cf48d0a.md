### Title
Bonus Reward Checkpoints Advanced Without Payment on Insufficient Pool During Redelegation Slash — (`File: x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is silently swallowed. However, by the time the error is returned, `processEventsAndClaimBonus` has already (1) decremented validator event reference counts in the store and (2) advanced the position's `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded` in-memory. The caller then persists the position with these advanced checkpoints via `setPositionWithState`. The result is that the position's accounting state records the bonus as "processed" while the owner receives nothing — a permanent, irrecoverable loss of accrued bonus rewards.

---

### Finding Description

`processEventsAndClaimBonus` performs two categories of side effects before it checks pool balance:

**Category 1 — store-persisted (irreversible within the call):**
Inside the event loop, `decrementEventRefCount` writes to the KV store for every processed event, potentially garbage-collecting events whose reference count reaches zero. [1](#0-0) 

**Category 2 — in-memory on `pos` (persisted by the caller):**
After the loop, `applyBonusAccrualCheckpoint` and `UpdateLastKnownBonded` advance the position's three bonus-tracking fields. [2](#0-1) 

Only **after** both categories of side effects does the function check pool balance and return `ErrInsufficientBonusPool`: [3](#0-2) 

In `slashRedelegationPosition`, the `ErrInsufficientBonusPool` branch is explicitly caught and execution continues: [4](#0-3) 

For a partial slash, the function then calls `setPositionWithState` with the in-memory `pos` that already has its checkpoints advanced: [5](#0-4) 

The three checkpoint fields — `LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded` — are now persisted as if the bonus was successfully claimed, even though zero coins were transferred to the owner. [6](#0-5) 

The next call to `processEventsAndClaimBonus` for this position will start from the advanced `LastEventSeq`, skipping all events that were already reference-count-decremented. The accrued bonus for those periods is permanently unclaimable.

---

### Impact Explanation

A position owner who has redelegated via `MsgTierRedelegate` permanently loses all accrued bonus rewards for the period between `LastBonusAccrual` and the slash block time, whenever:
- The destination validator is slashed (a normal protocol event), and
- The `RewardsPoolName` module account has insufficient balance to cover the owed bonus at that moment.

The loss is irrecoverable: the event reference counts are decremented (events may be garbage-collected), and the position's checkpoints are advanced past the owed window. No subsequent `MsgClaimTierRewards` call can recover the lost bonus.

The corrupted value is: `pos.LastBonusAccrual`, `pos.LastEventSeq`, `pos.LastKnownBonded` — advanced without the corresponding `bonusCoins` transfer to the owner's bank account. [7](#0-6) 

---

### Likelihood Explanation

The bonus pool can legitimately be empty or underfunded at any point — it is a module account funded externally and drained by normal user claims. Validator slashes are routine protocol events. Any user who has called `MsgTierRedelegate` and whose destination validator is subsequently slashed while the pool is empty will silently lose their accrued bonus. No special attacker action is required; the conditions arise from normal chain operation.

The entry path is fully unprivileged:
1. User calls `MsgTierRedelegate` → position enters `RedelegationMappings` [8](#0-7) 
2. Staking module slashes the destination validator → `BeforeRedelegationSlashed` hook fires [9](#0-8) 
3. `slashRedelegationPosition` runs, pool is empty → checkpoints advanced, bonus lost [10](#0-9) 

---

### Recommendation

When `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool` inside `slashRedelegationPosition`, the position's checkpoints must **not** be persisted with the advanced values. Two options:

1. **Snapshot and restore**: Before calling `processEventsAndClaimBonus`, snapshot `pos.LastBonusAccrual`, `pos.LastEventSeq`, and `pos.LastKnownBonded`. On `ErrInsufficientBonusPool`, restore the snapshot before calling `setPositionWithState`.

2. **Separate checkpoint advancement from payment**: Refactor `processEventsAndClaimBonus` so that checkpoint advancement only occurs after a successful `SendCoinsFromModuleToAccount`, making the function atomic with respect to both payment and state mutation.

Additionally, the event reference count decrements that occur inside the loop before the pool check should be guarded or rolled back on payment failure, to prevent premature garbage-collection of events the position has not actually been paid for.

---

### Proof of Concept

1. Alice calls `MsgLockTier` then `MsgTierRedelegate` to validator V2. A `RedelegationMappings` entry is created.
2. Alice accrues 30 days of bonus on V2 (non-zero `totalBonus`).
3. The `RewardsPoolName` account balance is zero (drained by other users or simply unfunded).
4. V2 is slashed. The staking module calls `BeforeRedelegationSlashed` → `slashRedelegationPosition`.
5. `processEventsAndClaimBonus` runs: decrements event ref counts in the store, advances `pos.LastBonusAccrual` to `blockTime`, advances `pos.LastEventSeq` to the latest event seq, then returns `ErrInsufficientBonusPool`.
6. `slashRedelegationPosition` logs the error and continues. For a partial slash, it calls `setPositionWithState(ctx, pos, nil)`, persisting the advanced checkpoints.
7. Alice later calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts from the now-advanced `LastEventSeq` and `LastBonusAccrual` — the 30-day window is gone. Alice receives zero bonus. The pool may now be funded, but the accrual window is permanently lost.

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L192-198)
```go
		segmentStart = evt.Timestamp
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-217)
```go
	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-241)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
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

**File:** x/tieredrewards/types/position.go (L65-69)
```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
	p.LastEventSeq = lastEventSeq
	p.LastBonusAccrual = t
	p.LastKnownBonded = lastKnownBonded
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L245-255)
```go
	completionTime, unbondingID, err := ms.redelegate(ctx, delAddr, srcValAddr, dstValAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
	}
	// unbondingID 0 means the source validator is unbonded; redelegation is instant.
	// We skip mapping because no asynchronous completion hook will trigger.
	if unbondingID != 0 {
		if err := ms.setRedelegationMapping(ctx, unbondingID, pos.Id); err != nil {
			return nil, err
		}
	}
```

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```
