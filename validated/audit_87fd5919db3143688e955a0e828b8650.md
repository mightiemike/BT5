### Title
Bonus Reward Checkpoint Advances Without Payment When Bonus Pool Is Drained During Redelegation Slash — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` mutates the position's `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded` checkpoints **and** decrements event reference counts in storage before it reaches the `sufficientBonusPoolBalance` guard. When the guard fails and returns `ErrInsufficientBonusPool`, `slashRedelegationPosition` swallows the error and then calls `setPositionWithState` with the already-mutated position. The result is that the position's checkpoints are permanently advanced with no bonus coins transferred to the owner.

---

### Finding Description

**Step 1 — Entry point.**
`BeforeRedelegationSlashed` (a standard Cosmos SDK staking hook) calls `slashRedelegationPosition`: [1](#0-0) 

**Step 2 — `processEventsAndClaimBonus` mutates state before the pool check.**
Inside the event loop, for every event processed:

- `pos.UpdateLastEventSeq(entry.Seq)` advances the checkpoint on the in-memory `pos` (passed as `*types.PositionState`).
- `k.decrementEventRefCount(ctx, valAddr, entry.Seq)` writes the decremented ref count **directly to storage** — this is not rolled back if the function later returns an error. [2](#0-1) 

After the loop, `applyBonusAccrualCheckpoint` and `UpdateLastKnownBonded` advance the remaining two checkpoints on the in-memory `pos`: [3](#0-2) 

Only **then** is the pool balance checked: [4](#0-3) 

If the pool is insufficient, `ErrInsufficientBonusPool` is returned with `pos` already fully mutated and ref counts already decremented in storage.

**Step 3 — Error is swallowed in `slashRedelegationPosition`.** [5](#0-4) 

**Step 4 — Mutated position is persisted.**
After swallowing the error, execution falls through to `setPositionWithState` with the already-mutated `pos`: [6](#0-5) 

Both the full-slash path (line 71) and the partial-slash path (line 77) call `setPositionWithState` unconditionally, persisting the advanced checkpoints.

---

### Impact Explanation

The position owner permanently loses all bonus rewards accrued since their last claim:

1. `LastEventSeq` is advanced → those validator events will never be replayed for this position.
2. `LastBonusAccrual` is advanced → the elapsed bonded time segment is erased.
3. `LastKnownBonded` is advanced → the bonded state is updated.
4. Event ref counts are decremented in storage → events may be garbage-collected, making recovery impossible even via a future fix.
5. No coins are sent to the owner.

This is a direct, permanent economic loss to the position owner proportional to the accrued bonus for the elapsed bonded segment.

---

### Likelihood Explanation

The precondition — bonus pool balance below the owed amount at the moment `BeforeRedelegationSlashed` fires — is realistic:

- The pool is a shared module account (`RewardsPoolName`) drained by all bonus claims across all positions. A large batch of claims in the same block, or a period of underfunding, can exhaust it.
- `BeforeRedelegationSlashed` fires automatically during any validator slash that touches a redelegation entry; no attacker action is required beyond having a position with a redelegation mapping.
- The scenario is locally reproducible: drain the pool to zero, then trigger a slash on a validator with a redelegation entry mapped to a position with pending bonus.

---

### Recommendation

Move all checkpoint mutations and ref-count decrements to **after** the `sufficientBonusPoolBalance` check succeeds and the `SendCoinsFromModuleToAccount` call succeeds. Alternatively, collect the events to process and the bonus amount first (read-only pass), verify the pool, transfer coins, and only then commit the mutations. This preserves the invariant: checkpoints advance if and only if the corresponding coins are delivered.

---

### Proof of Concept

```go
// 1. Create a position with a redelegation mapping and let bonus accrue.
// 2. Drain RewardsPoolName to zero via bankKeeper.
// 3. Call keeper.BeforeRedelegationSlashed(ctx, unbondingID, sharesToUnbond).
// 4. Assert:
//    - ownerBalance unchanged (no bonus paid)
//    - pos.LastBonusAccrual == blockTime  (checkpoint advanced)
//    - pos.LastEventSeq    == latestSeq   (checkpoint advanced)
// 5. Refill pool and call claimRewards for the same position.
// 6. Assert bonus returned is zero (rewards permanently lost).
```

The exact sequence maps to: `hooks.go:129` → `slash.go:54` → `claim_rewards.go:193,196,215,217,230` → `slash.go:56-63` → `slash.go:71 or 77`.

### Citations

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L193-198)
```go
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
