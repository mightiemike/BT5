### Title
Partial Redelegation Slash with Insufficient Bonus Pool Permanently Forfeits Bonus Rewards — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

In `processEventsAndClaimBonus`, `decrementEventRefCount` and `pos.UpdateLastEventSeq` are both called **inside the event loop**, before `sufficientBonusPoolBalance` is checked. When the pool check fails and `ErrInsufficientBonusPool` is returned, the store writes (reference-count decrements) and the in-memory `LastEventSeq` advance are already committed. In `slashRedelegationPosition`, this error is deliberately swallowed, and the position is then persisted with the advanced `LastEventSeq`. The slashed position can never reclaim those bonus rewards.

---

### Finding Description

**Step 1 — Event loop writes before pool check**

In `processEventsAndClaimBonus`:

```
for _, entry := range events {
    // ...bonus accumulation...
    pos.UpdateLastEventSeq(entry.Seq)          // ← in-memory advance
    if err := k.decrementEventRefCount(...);   // ← store write (may delete event)
}
// only AFTER the loop:
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err   // ← ErrInsufficientBonusPool returned here
}
``` [1](#0-0) [2](#0-1) 

When `ErrInsufficientBonusPool` is returned:
- All `decrementEventRefCount` store writes have already executed (events with `ReferenceCount == 1` are deleted).
- `pos.LastEventSeq` has been advanced in memory to the last processed event.
- No bonus has been transferred.

**Step 2 — Error is swallowed in `slashRedelegationPosition`**

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error(...)   // ← silently continues
    } else {
        return err
    }
}
``` [3](#0-2) 

**Step 3 — Partial slash path persists the advanced `LastEventSeq`**

For a partial slash (`sharesToUnbond < pos.Delegation.Shares`), `ClearBonusCheckpoints()` is **not** called. The position is persisted with the in-memory `LastEventSeq` that was advanced inside the loop:

```go
pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
return k.setPositionWithState(ctx, pos, nil)   // ← persists advanced LastEventSeq
``` [4](#0-3) 

`ClearBonusCheckpoints` (which resets `LastEventSeq` to 0) is only called on the full-slash branch: [5](#0-4) [6](#0-5) 

**Step 4 — Permanent loss**

On the next call to `processEventsAndClaimBonus` for this position, `getValidatorEventsSince` starts from the now-persisted `LastEventSeq`. The events that were processed during the failed slash settlement are skipped forever. The bonus for those segments is permanently forfeited. [7](#0-6) 

---

### Impact Explanation

A position owner who has redelegated via `TierRedelegate` and whose destination validator is subsequently partially slashed, at a moment when the `RewardsPoolName` module account balance is insufficient to cover the accrued bonus, permanently loses all bonus rewards that accrued since their last claim. The `LastEventSeq` and `LastBonusAccrual` checkpoints are advanced without any corresponding token transfer. The loss is irreversible because the checkpoints are persisted and the events are either deleted or will be skipped on future calls.

**Correction to the question's scope claim**: the impact is confined to the single slashed position. Other positions that share the same validator events are not harmed — `decrementEventRefCount` only decrements by 1 per call, so events with `ReferenceCount > 1` remain intact for other positions.

---

### Likelihood Explanation

The conditions are:
1. A position has been redelegated (`TierRedelegate` is a normal user-facing message).
2. The destination validator is slashed (a normal protocol event for a misbehaving validator).
3. The `RewardsPoolName` pool balance is below the accrued bonus at the time of the slash (possible if the pool is underfunded or has been heavily claimed).

All three conditions are reachable in normal production operation without any privileged access.

---

### Recommendation

Move `sufficientBonusPoolBalance` (and the `SendCoinsFromModuleToAccount` call) **before** the event loop, or — preferably — restructure `processEventsAndClaimBonus` so that all store mutations (`decrementEventRefCount`, `pos.UpdateLastEventSeq`) only execute after the pool check passes. Alternatively, wrap the entire function body in a cache-context and only commit on success, so that an `ErrInsufficientBonusPool` result leaves both the store and the in-memory position state unchanged.

---

### Proof of Concept

```
1. Create a tier position on validator V.
2. Call TierRedelegate to move the position to validator V2.
   → A redelegation mapping (unbondingId → positionId) is created.
3. Let some blocks pass so bonus accrues.
4. Drain the RewardsPoolName to zero (or below the accrued bonus).
5. Trigger a partial slash on V2 that fires BeforeRedelegationSlashed
   for the unbondingId created in step 2.
6. Observe:
   a. decrementEventRefCount was called for each event in the loop
      (store writes committed).
   b. pos.LastEventSeq was advanced in memory and then persisted by
      setPositionWithState.
   c. No coins were transferred from RewardsPoolName to the owner.
7. Call ClaimRewards (or any path that calls processEventsAndClaimBonus)
   for the same position.
8. Assert: getValidatorEventsSince returns no events (all skipped),
   totalBonus == 0, owner balance unchanged.
   → Bonus rewards for the pre-slash accrual period are permanently lost.
```

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L153-156)
```go
	events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
	if err != nil {
		return nil, err
	}
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L54-64)
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

**File:** x/tieredrewards/types/position.go (L80-84)
```go
func (p *Position) ClearBonusCheckpoints() {
	p.LastBonusAccrual = time.Time{}
	p.LastEventSeq = 0
	p.LastKnownBonded = false
}
```
