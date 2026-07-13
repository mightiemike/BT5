### Title
Bonus Accrual Checkpoint Advances Without Payment on Insufficient Pool During Redelegation Slash — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is deliberately swallowed to prevent a chain halt. However, `processEventsAndClaimBonus` has already mutated the in-memory `pos` — advancing `LastBonusAccrual`, `LastEventSeq`, and `LastKnownBonded` — **before** the pool balance check. The subsequent `setPositionWithState` call then persists these advanced checkpoints to the store without any payment having occurred. All bonus rewards accrued for the period `[original_LastBonusAccrual, slash_blockTime]` are permanently unclaimable.

---

### Finding Description

The ordering bug is in `processEventsAndClaimBonus`:

**Step 1 — Checkpoints and event ref counts are mutated/decremented before the pool check:**

Inside the event loop (lines 193, 196), `pos.UpdateLastEventSeq(entry.Seq)` and `k.decrementEventRefCount(...)` are called for every event. These are both in-memory mutations and persisted store writes respectively. [1](#0-0) 

**Step 2 — `applyBonusAccrualCheckpoint` advances `LastBonusAccrual` unconditionally, before the pool check:** [2](#0-1) 

**Step 3 — Only then is the pool balance checked:** [3](#0-2) 

When the pool is empty and `totalBonus > 0`, `ErrInsufficientBonusPool` is returned — but `pos` (passed as `&pos`) already has `LastBonusAccrual = blockTime`, `LastEventSeq` advanced past all events, and `LastKnownBonded` updated.

**Step 4 — `slashRedelegationPosition` swallows the error:** [4](#0-3) 

**Step 5 — For a partial slash, `setPositionWithState` persists the mutated `pos`:** [5](#0-4) 

The full-slash branch at line 70 calls `pos.ClearBonusCheckpoints()` before persisting, so it is not affected. The partial-slash branch has no such reset. [6](#0-5) 

There is a second compounding issue: `decrementEventRefCount` is called inside the loop for every event before the pool check. These are store writes that survive the error return. The events' reference counts are decremented even though no bonus was paid, potentially causing premature event pruning and making the lost rewards unrecoverable even if the pool is later refunded. [7](#0-6) 

---

### Impact Explanation

After the partial redelegation slash with an empty pool:

- `pos.LastBonusAccrual` is persisted as `blockTime` (the slash block time)
- `pos.LastEventSeq` is persisted past all events that covered the accrual period
- All future calls to `claimRewards` or `processEventsAndClaimBonus` use `segmentStart = pos.LastBonusAccrual`, which is now `blockTime`

The bonus for the entire period `[original_LastBonusAccrual, blockTime]` is permanently lost. The position owner receives zero bonus for that period even after the pool is refunded. This is a direct, irreversible fund loss for the position owner.

---

### Likelihood Explanation

This requires no malicious action. The conditions are:

1. A position is in a redelegating state (normal user action: redelegate)
2. The source validator is slashed while the redelegation is in the unbonding period (normal chain event)
3. The `RewardsPoolName` module account balance is insufficient to cover the accrued bonus at the time of the slash

Condition 3 is realistic: the pool can be drained by normal reward claims, or it may simply not have been topped up. The pool is not guaranteed to always be solvent. No privileged access or operator compromise is required.

---

### Recommendation

Move `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` to **after** the successful `SendCoinsFromModuleToAccount` call, so checkpoints only advance when payment actually succeeds. Similarly, `decrementEventRefCount` should not be called until payment is confirmed, or the function should be restructured to collect events to decrement only after a successful transfer.

Alternatively, in `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, explicitly reset the in-memory `pos` checkpoints back to their pre-call values before calling `setPositionWithState`.

---

### Proof of Concept

```
1. Create a tier position with a delegation to validator V1.
2. Redelegate from V1 to V2 — this creates a redelegation entry with an unbondingId.
3. Advance block time by 30 days (bonus accrues).
4. Drain the RewardsPoolName module account to zero.
5. Slash V1 (the source validator) — this triggers BeforeRedelegationSlashed
   → slashRedelegationPosition → processEventsAndClaimBonus → ErrInsufficientBonusPool
   → error swallowed → setPositionWithState persists advanced LastBonusAccrual.
6. Fund the RewardsPoolName with sufficient tokens.
7. Call MsgClaimTierRewards for the position.
8. Assert: owner receives zero bonus for the 30-day period (bonus is permanently lost).
   The position's LastBonusAccrual equals the slash block time, so claimRewards
   computes segmentStart = slash_blockTime and finds no accrual since then.
```

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

**File:** x/tieredrewards/keeper/slash.go (L68-72)
```go
	if fullSlash {
		pos.Delegation = nil
		pos.ClearBonusCheckpoints()
		return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
	}
```

**File:** x/tieredrewards/keeper/slash.go (L76-77)
```go
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```
