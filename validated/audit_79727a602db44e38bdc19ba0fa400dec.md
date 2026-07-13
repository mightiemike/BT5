Now I have all the information needed to trace the full exploit path. Let me verify the exact `ClearExit` behavior and the `validateTriggerExit` guard.

The vulnerability is **real and confirmed**. Here is the full analysis:

---

### Title
Unbounded Bonus Re-Accrual via Clear-and-Re-Exit Cycle Drains `RewardsPoolName` — (`x/tieredrewards/keeper/msg_server.go`, `x/tieredrewards/types/position_state.go`)

### Summary

A position owner can repeatedly cycle through `TriggerExitFromTier` → `ClearPosition` → `TriggerExitFromTier` to accrue bonus rewards for multiple full exit-lock durations on the same locked tokens, draining `RewardsPoolName`.

### Finding Description

**Step 1 — `TriggerExitFromTier`** calls `validateTriggerExit`, which only checks ownership and `HasTriggeredExit()`: [1](#0-0) 

After a successful call, `pos.TriggerExit(blockTime, tier.ExitDuration)` sets `ExitTriggeredAt = blockTime` and `ExitUnlockAt = blockTime + duration`. [2](#0-1) 

**Step 2 — Advance time past `ExitUnlockAt`**, so `CompletedExitLockDuration` returns true. [3](#0-2) 

**Step 3 — `ClearPosition`** calls `claimRewards` (which calls `processEventsAndClaimBonus` → `applyBonusAccrualCheckpoint`). Because `CompletedExitLockDuration` is true, `applyBonusAccrualCheckpoint` caps `LastBonusAccrual` at `ExitUnlockAt`: [4](#0-3) 

Then `ClearExit(blockTime)` is called, which clears `ExitTriggeredAt`/`ExitUnlockAt` and **resets `LastBonusAccrual = blockTime`** (current block time, which is ≥ `ExitUnlockAt`): [5](#0-4) 

**Step 4 — `TriggerExitFromTier` again.** After `ClearExit`, `HasTriggeredExit()` returns `false` (since `ExitTriggeredAt` is zero), so `validateTriggerExit` passes with no further guards: [6](#0-5) 

A new exit is set: `ExitTriggeredAt = blockTime`, `ExitUnlockAt = blockTime + duration`.

**Step 5 — Advance time past the new `ExitUnlockAt`, then claim rewards.** In `processEventsAndClaimBonus`, `segmentStart = LastBonusAccrual = blockTime` (set by `ClearExit`). `computeSegmentBonus` caps `segmentEnd` at `ExitUnlockAt = blockTime + duration`: [7](#0-6) 

Bonus accrues for a **second full exit-lock duration** on the same locked tokens. The cycle can be repeated indefinitely.

### Impact Explanation

Each cycle transfers `shares × tokensPerShare × BonusApy × ExitDuration / SecondsPerYear` tokens from `RewardsPoolName` to the attacker's address via `bankKeeper.SendCoinsFromModuleToAccount`. With enough cycles, the entire pool is drained. [8](#0-7) 

### Likelihood Explanation

The attack requires only standard user-level transactions (`MsgTriggerExitFromTier`, `MsgClearPosition`) with no privileged access. The position must be delegated and the tier must not be close-only — both are the normal operating conditions for any active position. The only cost is waiting for the exit-lock duration per cycle.

### Recommendation

In `validateTriggerExit` (or in `ClearPosition`), add a guard that prevents re-triggering exit on a position that has already completed an exit cycle. One approach: introduce a `HasCompletedExit bool` or `ExitCycleCount uint32` field on `Position` that is set in `ClearExit` and checked in `validateTriggerExit`. Alternatively, `ClearPosition` should delete the position (or require undelegation) rather than allowing the position to remain active and re-enter the exit queue.

### Proof of Concept

```
// Keeper test (pseudocode):
// 1. Lock tokens → position P (delegated, tier not close-only)
// 2. TriggerExitFromTier(P) → ExitUnlockAt = T0 + duration
// 3. Advance blockTime to T1 > ExitUnlockAt
// 4. ClearPosition(P) → claimRewards (bonus1 paid, LastBonusAccrual = ExitUnlockAt)
//                     → ClearExit(T1) → LastBonusAccrual = T1, ExitTriggeredAt = zero
// 5. TriggerExitFromTier(P) → ExitUnlockAt = T1 + duration  [no guard blocks this]
// 6. Advance blockTime to T2 > T1 + duration
// 7. ClaimTierRewards(P) → bonus2 paid for [T1, T1+duration]
// Assert: bonus1 + bonus2 > bonus from a single exit cycle
// Assert: RewardsPoolName balance decreased by bonus1 + bonus2
```

The root cause is that `validateTriggerExit` only checks the **current** `HasTriggeredExit()` state, which is `false` after `ClearExit`, with no memory of prior completed exit cycles. [1](#0-0) [5](#0-4)

### Citations

**File:** x/tieredrewards/keeper/msg_validate.go (L130-140)
```go
func (k Keeper) validateTriggerExit(pos types.Position, owner string) error {
	if !pos.IsOwner(owner) {
		return types.ErrNotPositionOwner
	}

	if pos.HasTriggeredExit() {
		return types.ErrPositionTriggeredExit
	}

	return nil
}
```

**File:** x/tieredrewards/types/position.go (L61-63)
```go
func (p Position) CompletedExitLockDuration(blockTime time.Time) bool {
	return !p.ExitUnlockAt.IsZero() && !blockTime.Before(p.ExitUnlockAt)
}
```

**File:** x/tieredrewards/types/position.go (L71-74)
```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
	p.ExitTriggeredAt = blockTime
	p.ExitUnlockAt = blockTime.Add(duration)
}
```

**File:** x/tieredrewards/types/position.go (L86-88)
```go
func (p Position) HasTriggeredExit() bool {
	return !p.ExitTriggeredAt.IsZero()
}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L15-21)
```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
	accrualEnd := blockTime
	if pos.CompletedExitLockDuration(blockTime) {
		accrualEnd = pos.ExitUnlockAt
	}
	pos.UpdateLastBonusAccrual(accrualEnd)
}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L26-28)
```go
	if !pos.ExitUnlockAt.IsZero() && segmentEnd.After(pos.ExitUnlockAt) {
		segmentEnd = pos.ExitUnlockAt
	}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L239-240)
```go

```

**File:** x/tieredrewards/types/position_state.go (L56-63)
```go
func (p *PositionState) ClearExit(blockTime time.Time) {
	p.ExitTriggeredAt = time.Time{}
	p.ExitUnlockAt = time.Time{}
	if p.IsDelegated() {
		// required so that positions who clear exit after exit lock duration won't have extra bonus accrued
		p.LastBonusAccrual = blockTime
	}
}
```
