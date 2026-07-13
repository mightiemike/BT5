### Title
Bonus Rewards Permanently Lost When `ErrInsufficientBonusPool` Is Swallowed in `slashRedelegationPosition` — (`x/tieredrewards/keeper/slash.go`, `claim_rewards.go`)

---

### Summary

`slashRedelegationPosition` deliberately swallows `ErrInsufficientBonusPool` to prevent a chain halt. However, by the time `processEventsAndClaimBonus` returns that error, it has already (a) advanced all three position checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) in the in-memory `pos` struct and (b) decremented (and possibly deleted) every processed event's reference count in the store. The caller then persists the mutated `pos` via `setPositionWithState`. The bonus for the entire processed period is permanently unrecoverable.

---

### Finding Description

**Step 1 — Entry point.**
`BeforeRedelegationSlashed` (a standard Cosmos SDK staking hook) calls `slashRedelegationPosition`. [1](#0-0) 

**Step 2 — `processEventsAndClaimBonus` mutates `pos` before the pool check.**

Inside the event loop, for every event:
- `pos.UpdateLastEventSeq(entry.Seq)` advances `LastEventSeq` in the pointer-passed struct.
- `decrementEventRefCount` decrements (and deletes when count reaches zero) the event from the store. [2](#0-1) 

After the loop, still before the pool check:
- `applyBonusAccrualCheckpoint` advances `LastBonusAccrual` to the current block time.
- `pos.UpdateLastKnownBonded(bonded)` updates `LastKnownBonded`. [3](#0-2) 

Only then does `sufficientBonusPoolBalance` run. If the pool is empty it returns `ErrInsufficientBonusPool`, and the function exits **before** `SendCoinsFromModuleToAccount` is ever reached. [4](#0-3) 

**Step 3 — Error is swallowed; mutated `pos` is persisted.**

`slashRedelegationPosition` catches `ErrInsufficientBonusPool`, logs it, and continues. Because `pos` was passed as `&pos`, all three checkpoint fields are already updated in the local variable. The function then calls `setPositionWithState` with this mutated state. [5](#0-4) 

`setPositionWithState` writes the position to the store unconditionally. [6](#0-5) 

**Step 4 — Permanent data loss.**

On the next call to `processEventsAndClaimBonus` for this position:
- `getValidatorEventsSince` starts from the new (advanced) `LastEventSeq` — the already-decremented events are gone.
- The segment bonus is computed from the new `LastBonusAccrual` (current block time at slash), not the original checkpoint.
- The entire bonus accrued from the old `LastBonusAccrual` up to the slash block is permanently uncomputable and unpayable. [7](#0-6) 

---

### Correction to the Question's Framing

The question states that `setPositionWithState` is called with "updated `LastEventSeq` but **stale** `LastBonusAccrual` and `LastKnownBonded`." This is **factually incorrect**. All three fields are updated *before* `sufficientBonusPoolBalance` is called (lines 193, 215, 217). The actual invariant violated is: **"if checkpoints are advanced and events are decremented, the corresponding bonus must have been paid."** That invariant is broken, not the atomicity of the three fields with each other.

---

### Impact Explanation

A position holder permanently loses all bonus rewards accrued from their last claim up to the slash block. The events needed to recompute that bonus are deleted from the store (reference count decremented to zero), and the checkpoints are advanced past them. No future claim can recover this amount. This is an irreversible, per-position fund loss triggered by a legitimate on-chain slash event combined with a depleted bonus pool.

---

### Likelihood Explanation

The bonus pool (`types.RewardsPoolName`) is a module account funded externally. It can be legitimately depleted through normal reward claims. A validator slash on a redelegation is a standard Cosmos SDK event. No attacker control is required — this can occur in normal operation whenever a slash fires while the pool balance is insufficient to cover the accrued bonus.

---

### Recommendation

The fix must ensure that either:
1. **No state is mutated before the pool check** — restructure `processEventsAndClaimBonus` to compute `totalBonus` and collect all mutations, then atomically apply them only after confirming the pool is sufficient; or
2. **The error path in `slashRedelegationPosition` rolls back the in-memory mutations** — reload `pos` from the store after swallowing `ErrInsufficientBonusPool` before calling `setPositionWithState`; or
3. **Partial payment** — pay whatever the pool can cover and advance checkpoints only proportionally.

Option 1 is cleanest: defer all writes to `pos` (LastEventSeq, LastBonusAccrual, LastKnownBonded) and all `decrementEventRefCount` calls until after `sufficientBonusPoolBalance` succeeds.

---

### Proof of Concept

```go
// Keeper test outline:
// 1. Create a position delegated to a bonded validator.
// 2. Append a SLASH validator event (simulating BeforeValidatorSlashed).
// 3. Drain the RewardsPoolName module account to zero.
// 4. Call slashRedelegationPosition (simulating BeforeRedelegationSlashed).
//    → ErrInsufficientBonusPool is swallowed; pos checkpoints are advanced.
// 5. Refund the bonus pool.
// 6. Call processEventsAndClaimBonus again (simulating a normal ClaimRewards).
// 7. Assert totalBonus == 0 (no events remain, segment starts at slash time).
//    Expected (correct): totalBonus > 0 for the pre-slash accrual period.
//    Actual (buggy):     totalBonus == 0 — rewards permanently lost.
```

### Citations

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-198)
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

**File:** x/tieredrewards/keeper/position.go (L126-141)
```go
func (k Keeper) setPositionWithState(ctx context.Context, state types.PositionState, update *ValidatorTransition) error {
	if err := state.Validate(); err != nil {
		return err
	}

	pos := state.Position

	oldPos, err := k.getPosition(ctx, pos.Id)
	isNew := errors.Is(err, types.ErrPositionNotFound)
	if !isNew && err != nil {
		return err
	}

	if err := k.Positions.Set(ctx, pos.Id, pos); err != nil {
		return err
	}
```
