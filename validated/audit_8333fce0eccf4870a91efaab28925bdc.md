### Title
Checkpoint Advanced Before Pool-Balance Check Causes Permanent Bonus Loss on Redelegation Slash — (`x/tieredrewards/keeper/claim_rewards.go`, `slash.go`)

---

### Summary

`processEventsAndClaimBonus` advances `pos.LastBonusAccrual` to `blockTime` **before** it checks whether the rewards pool holds enough funds. When the pool is insufficient, the function returns `ErrInsufficientBonusPool` with the checkpoint already mutated. `slashRedelegationPosition` deliberately swallows that error to avoid a chain halt, then calls `setPositionWithState` with the mutated `pos`, persisting the advanced checkpoint. The accrual window is permanently erased; the owner can never reclaim it.

---

### Finding Description

**Step 1 — Entry point**

`BeforeRedelegationSlashed` (a staking hook) fires during `SlashRedelegation` in the SDK staking module. It is a supported production path triggered by any evidence-based or downtime slash that touches a redelegation entry. [1](#0-0) 

**Step 2 — `slashRedelegationPosition` calls `processEventsAndClaimBonus` via pointer**

The position is passed as `&pos`, so any mutation inside `processEventsAndClaimBonus` is visible to the caller after the call returns. [2](#0-1) 

**Step 3 — Checkpoint is advanced BEFORE the pool-balance check**

Inside `processEventsAndClaimBonus`:

```
line 215: applyBonusAccrualCheckpoint(&pos.Position, blockTime)   // ← mutates pos
line 217: pos.UpdateLastKnownBonded(bonded)
...
line 230: if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
line 231:     return nil, err   // ← returns ErrInsufficientBonusPool with pos already mutated
``` [3](#0-2) 

`applyBonusAccrualCheckpoint` sets `pos.LastBonusAccrual = blockTime` unconditionally. [4](#0-3) 

**Step 4 — Error is swallowed; mutated `pos` is persisted**

Back in `slashRedelegationPosition`, the `ErrInsufficientBonusPool` branch only logs and falls through. Execution reaches `setPositionWithState(ctx, pos, nil)`, which writes the position — now carrying `LastBonusAccrual = blockTime` — to the store. [5](#0-4) 

**Step 5 — No recourse**

Any subsequent `ClaimTierRewards` call will call `processEventsAndClaimBonus` again. `segmentStart` is read from `pos.LastBonusAccrual`, which is now `blockTime`. The entire accrual window `[original_checkpoint, blockTime]` is gone; the function computes zero bonus for that period. [6](#0-5) 

---

### Impact Explanation

The position owner permanently loses all bonus rewards that accrued between their previous `LastBonusAccrual` and the slash block time. The funds remain in the `RewardsPoolName` module account but are now unreachable by the owner — a direct accounting loss of backed assets from the intended module/account boundary. This matches the Critical scope: *"escrow accounting flaw loses backed assets or lets value leave the intended module/account boundary."*

---

### Likelihood Explanation

The rewards pool can be drained by normal operation (many positions claiming simultaneously, or a period of high accrual). A slash event is an externally triggered, non-privileged chain event. No attacker control is required; the condition can arise organically. An adversary who can observe pool balance and time a redelegation slash (e.g., by controlling a validator and triggering a double-sign) can reliably trigger this.

---

### Recommendation

Move `applyBonusAccrualCheckpoint` (and `UpdateLastKnownBonded`) to **after** the successful `SendCoinsFromModuleToAccount` call, so the checkpoint is only advanced when the bonus is actually paid. If the pool is insufficient, return without mutating `pos`, allowing the caller to retry or handle the shortfall without data loss.

---

### Proof of Concept

```go
// 1. Create a position with a non-zero accrual period (e.g., 1 day elapsed).
// 2. Drain the rewards pool to 1uatom (below any non-trivial bonus).
// 3. Trigger BeforeRedelegationSlashed for the position's unbonding ID.
//    → slashRedelegationPosition → processEventsAndClaimBonus returns ErrInsufficientBonusPool
//    → error logged, pos.LastBonusAccrual advanced to blockTime, persisted.
// 4. Call ClaimTierRewards for the position owner.
// 5. Assert: owner received 0 bonus despite a non-zero accrual period.
//    Assert: pos.LastBonusAccrual == blockTime (checkpoint advanced past the accrual window).
```

### Citations

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L163-166)
```go
	// This prevents overpaying bonus for unbonded gaps between claims.
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual

```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-231)
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
