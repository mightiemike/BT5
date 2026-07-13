### Title
Silent Permanent Forfeiture of Accrued Bonus Rewards on Insufficient Pool During Redelegation Slash — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`slashRedelegationPosition` deliberately swallows `ErrInsufficientBonusPool` returned by `processEventsAndClaimBonus`. However, by the time that error is returned, `processEventsAndClaimBonus` has already advanced the position's in-memory checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) and decremented event reference counts in the store. `slashRedelegationPosition` then persists the modified position via `setPositionWithState`, making the checkpoint advancement permanent. The bonus is never paid, and the position owner has no mechanism to recover it.

---

### Finding Description

**Step 1 — Entry point.**
The `BeforeRedelegationSlashed` staking hook fires when the SDK's `SlashRedelegation` logic unbonds shares from a redelegation entry. [1](#0-0) 

It delegates unconditionally to `slashRedelegationPosition`.

**Step 2 — Checkpoint advancement happens before the pool check.**
Inside `processEventsAndClaimBonus`, the event-replay loop runs first: [2](#0-1) 

For every event, `pos.UpdateLastEventSeq(entry.Seq)` (line 193) and `k.decrementEventRefCount(...)` (line 196) are called. After the loop, the accrual checkpoint and bonded state are also updated: [3](#0-2) 

Only **after** all of this does the pool-balance guard run: [4](#0-3) 

When the pool is empty, `ErrInsufficientBonusPool` is returned with `pos` already mutated and event ref-counts already decremented in the store.

**Step 3 — The error is swallowed and the mutated position is persisted.**
`slashRedelegationPosition` catches `ErrInsufficientBonusPool`, logs it, and continues: [5](#0-4) 

It then calls `setPositionWithState` with the already-mutated `pos`: [6](#0-5) 

The advanced `LastEventSeq` and `LastBonusAccrual` are now persisted. The events whose ref-counts were decremented may be garbage-collected. There is no replay path that can recover the forfeited bonus.

---

### Impact Explanation

The position owner permanently loses legitimately accrued bonus rewards. The amount is proportional to the position's delegated shares, the tier's `BonusApy`, and the time elapsed since the last claim. Because the checkpoints are advanced and event ref-counts decremented, no subsequent `claimRewards` call can recompute or recover the lost amount. This is a direct, irreversible loss of user funds held in the `RewardsPoolName` module account that were owed to the position owner. [7](#0-6) 

---

### Likelihood Explanation

Two independently plausible conditions must coincide:

1. **Pool depletion** — The `RewardsPoolName` balance reaches zero. This happens naturally as positions claim rewards over time, or can be accelerated by any user claiming their own rewards (an unprivileged `MsgClaimRewards` transaction).
2. **Redelegation slash** — Any validator with an active redelegation entry linked to a tiered position is slashed (double-sign or downtime). This is a normal chain event requiring no attacker privilege beyond controlling a validator.

Neither condition is exotic. The combination is reachable in production without any privileged access.

---

### Recommendation

Move the `sufficientBonusPoolBalance` check **before** any mutation of `pos` or any store write (i.e., before the event-replay loop). If the pool is insufficient, return the error without advancing checkpoints or decrementing ref-counts, so the position owner can claim the bonus once the pool is replenished. Alternatively, record the owed-but-unpaid bonus in a separate debt ledger and pay it when the pool is next funded, rather than silently forfeiting it.

---

### Proof of Concept

```go
// Keeper test outline (no external dependencies):
// 1. Create a tier position with an active delegation to validator V.
// 2. Advance time so bonus accrues.
// 3. Drain the RewardsPoolName to zero (send all coins out via test helper).
// 4. Create a redelegation mapping entry for the position's unbondingId.
// 5. Call k.BeforeRedelegationSlashed(ctx, unbondingId, sharesToUnbond).
// 6. Assert:
//    a. ownerBalanceBefore == ownerBalanceAfter  (no bonus paid)
//    b. position.LastBonusAccrual == blockTime   (checkpoint advanced)
//    c. position.LastEventSeq advanced past the slash event
// 7. Replenish the pool.
// 8. Call k.ClaimRewards for the position.
// 9. Assert bonus received == 0  (permanently lost, not recoverable).
```

The test will pass steps 6a–6c and fail step 9 with zero bonus, confirming permanent loss.

### Citations

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
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

**File:** x/tieredrewards/keeper/slash.go (L68-77)
```go
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

**File:** x/tieredrewards/types/keys.go (L23-23)
```go
	RewardsPoolName = "rewards_pool"
```
