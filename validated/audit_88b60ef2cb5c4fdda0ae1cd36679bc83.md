### Title
Checkpoint Advancement Without Bonus Transfer on `ErrInsufficientBonusPool` in `slashRedelegationPosition` Permanently Destroys Accrued Bonus Rewards — (`x/tieredrewards/keeper/claim_rewards.go`, `x/tieredrewards/keeper/slash.go`)

---

### Summary

`processEventsAndClaimBonus` advances all reward-accounting checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) and permanently decrements event reference counts **before** it checks whether the rewards pool has sufficient balance. When the pool is empty, it returns `ErrInsufficientBonusPool`. `slashRedelegationPosition` deliberately swallows that error to avoid a chain halt, then calls `setPositionWithState` with the already-advanced in-memory `pos`. The result is that the position's checkpoints are persisted as if the bonus was paid, even though no coins were transferred. The accrued bonus for the pre-slash window is permanently unclaimable.

---

### Finding Description

**Step 1 — Entry point.**
`BeforeRedelegationSlashed` (hooks.go:128-130) is a standard Cosmos SDK staking hook that fires during `SlashRedelegation`. It calls `slashRedelegationPosition`. [1](#0-0) 

**Step 2 — `slashRedelegationPosition` calls `processEventsAndClaimBonus` and swallows `ErrInsufficientBonusPool`.** [2](#0-1) 

The comment says "Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt." Execution continues with the modified `pos` regardless.

**Step 3 — Inside `processEventsAndClaimBonus`, checkpoints advance before the pool check.**

The event loop at lines 172–199 calls `pos.UpdateLastEventSeq(entry.Seq)` (in-memory) and `k.decrementEventRefCount(...)` (**persistent store write**) for every event: [3](#0-2) 

After the loop, `applyBonusAccrualCheckpoint` and `UpdateLastKnownBonded` are called: [4](#0-3) 

Only **after** all of the above does the code check pool balance: [5](#0-4) 

When the pool is empty, `return nil, err` exits with the in-memory `pos` already carrying advanced `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded`, and with the persistent event reference counts already decremented.

**Step 4 — `setPositionWithState` persists the advanced checkpoints.**

Back in `slashRedelegationPosition`, after the error is swallowed, the function falls through to: [6](#0-5) 

`setPositionWithState` writes the advanced `pos` to `k.Positions`: [7](#0-6) 

**Step 5 — Future claims skip the lost window.**

Any subsequent call to `processEventsAndClaimBonus` for this position reads `pos.LastEventSeq` and `pos.LastBonusAccrual` from the store. Because they were advanced past the pre-slash events, `getValidatorEventsSince` returns no events for that period, and `segmentStart` is already set to `blockTime` of the slash. The pre-slash accrual window is permanently closed. [8](#0-7) 

---

### Impact Explanation

The position owner permanently loses all bonus rewards accrued between their last successful claim and the moment of the redelegation slash when the pool is empty. The loss is irreversible: the checkpoint window is closed, the event reference counts are decremented (events may be garbage-collected), and no recovery path exists in the protocol. This is a direct, quantifiable loss of funds owed to the position owner.

---

### Likelihood Explanation

The `RewardsPoolName` module account is funded by governance or external top-ups. It can legitimately reach zero (pool exhausted, delayed refill, or governance inaction). A redelegation slash is a normal protocol event (double-sign or downtime on the source validator during the redelegation unbonding period). The two conditions co-occurring is realistic and requires no attacker — it is a passive loss triggered by normal chain operation.

---

### Recommendation

Move `applyBonusAccrualCheckpoint`, `pos.UpdateLastKnownBonded`, and all `decrementEventRefCount` calls to **after** the successful `SendCoinsFromModuleToAccount`. If the pool is insufficient, either:
- Return the error without advancing any checkpoint (let the caller retry later), or
- Advance checkpoints only after coins are confirmed sent.

The "prevent chain halt" intent can still be preserved by returning `nil` from `slashRedelegationPosition` after logging, but only if no checkpoint mutation has occurred. Alternatively, the pool should be kept solvent by protocol invariant (e.g., a minimum reserve enforced at governance level).

---

### Proof of Concept

```go
// keeper test outline
func TestSlashRedelegationWithEmptyPool(t *testing.T) {
    // 1. Create a position with an active redelegation, let time pass to accrue bonus.
    // 2. Drain RewardsPoolName to zero.
    // 3. Trigger BeforeRedelegationSlashed (simulate via keeper.Hooks().BeforeRedelegationSlashed).
    // 4. Refill RewardsPoolName.
    // 5. Call ClaimTierRewards for the position owner.
    // 6. Assert bonus received == 0 for the pre-slash accrual period.
    //    (Without the bug, the owner should receive the accrued bonus after refill.)
}
```

The test is locally runnable against the unmodified keeper using the existing `keeper_helpers_test.go` scaffolding. The assertion at step 6 will pass (owner gets zero), confirming the checkpoint was advanced without payment.

### Citations

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
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

**File:** x/tieredrewards/keeper/slash.go (L76-77)
```go
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L153-165)
```go
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
```

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

**File:** x/tieredrewards/keeper/position.go (L139-141)
```go
	if err := k.Positions.Set(ctx, pos.Id, pos); err != nil {
		return err
	}
```
