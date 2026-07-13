### Title
Silent Checkpoint Advancement on `ErrInsufficientBonusPool` Permanently Forfeits Accrued Bonus Rewards — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

When `BeforeRedelegationSlashed` fires and the `RewardsPoolName` module account cannot cover the computed bonus, `slashRedelegationPosition` swallows `ErrInsufficientBonusPool` and then persists the position with already-advanced `LastBonusAccrual`, `LastKnownBonded`, and `LastEventSeq` checkpoints. The accrued bonus for that period is permanently unrecoverable.

---

### Finding Description

**Step 1 — Hook entry point.**
`BeforeRedelegationSlashed` delegates unconditionally to `slashRedelegationPosition`: [1](#0-0) 

**Step 2 — `processEventsAndClaimBonus` advances checkpoints before the pool check.**
Inside `processEventsAndClaimBonus`, the event loop decrements reference counts and updates `LastEventSeq` in memory, then: [2](#0-1) 

After the loop, both `LastBonusAccrual` and `LastKnownBonded` are advanced unconditionally: [3](#0-2) 

Only *after* these mutations does the pool balance check run: [4](#0-3) 

When the pool is empty, `ErrInsufficientBonusPool` is returned — but the `pos` pointer already carries the advanced checkpoints.

**Step 3 — Error is swallowed in `slashRedelegationPosition`.** [5](#0-4) 

Execution continues with the mutated `pos`.

**Step 4 — Position is persisted with advanced checkpoints (partial-slash path).** [6](#0-5) 

The saved position now has `LastBonusAccrual = blockTime` and `LastKnownBonded` reflecting the post-event state, as if the bonus had been paid.

**Step 5 — Next claim computes zero bonus for the skipped segment.**
On the next `ClaimTierRewards`, `processEventsAndClaimBonus` reads `segmentStart = pos.LastBonusAccrual` (already at `blockTime`) and finds no elapsed time for the period that was silently consumed, yielding zero bonus. [7](#0-6) 

---

### Impact Explanation

A position owner permanently loses all bonus rewards accrued up to the moment `BeforeRedelegationSlashed` fires, whenever the `RewardsPoolName` balance is below the computed bonus at that instant. The loss is irreversible: the checkpoints are advanced, the validator events are consumed (reference counts decremented), and no retry or recovery path exists. This is a direct, quantifiable fund loss matching the High cross-module invariant scope (tieredrewards × bank × staking).

---

### Likelihood Explanation

The `RewardsPoolName` pool can be transiently empty or underfunded during normal chain operation (e.g., between inflation distribution cycles, or if the pool is drained by concurrent claims). A redelegation slash is a standard staking event triggered by evidence submission — no attacker privilege is required. Any validator slash that hits a redelegating position while the pool is low triggers the bug automatically.

---

### Recommendation

Move the `sufficientBonusPoolBalance` check **before** any checkpoint or event-sequence mutation inside `processEventsAndClaimBonus`. If the pool is insufficient, return the error without touching `LastBonusAccrual`, `LastKnownBonded`, or `LastEventSeq`, so the next claim can retry the full period. Alternatively, accumulate the owed bonus as a pending debt record rather than silently discarding it.

---

### Proof of Concept

1. Create a tier position with an active redelegation; let several blocks pass so bonus accrues.
2. Drain `RewardsPoolName` to zero via `bankKeeper.SendCoinsFromModuleToAccount` in the test setup.
3. Call `slashRedelegationPosition` (or fire `BeforeRedelegationSlashed`) with a partial `sharesToUnbond`.
4. Verify no error is returned and the position's `LastBonusAccrual` equals `blockTime`.
5. Refill `RewardsPoolName` with sufficient funds.
6. Call `ClaimTierRewards` for the owner.
7. Assert the owner receives **zero** bonus — the accrued segment was silently forfeited at step 3.

The root cause is the ordering in `processEventsAndClaimBonus`: [8](#0-7) 

combined with the unconditional error suppression in `slashRedelegationPosition`: [9](#0-8)

### Citations

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L164-165)
```go
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L193-198)
```go
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
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
