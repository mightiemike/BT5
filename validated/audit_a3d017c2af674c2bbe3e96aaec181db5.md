Looking at the exact code flow in `processEventsAndClaimBonus` and `slashRedelegationPosition` to determine if the checkpoint-advance-before-pool-check ordering creates a real loss.

The code path is fully confirmed. Here is the precise execution trace:

**`processEventsAndClaimBonus` (claim_rewards.go:142–252) modifies `pos` in-place BEFORE the pool check:**

1. Loop (lines 172–198): `pos.UpdateLastEventSeq(entry.Seq)` advances `LastEventSeq` and `decrementEventRefCount` consumes validator events for each processed entry.
2. **Line 215**: `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` — writes `pos.LastBonusAccrual = blockTime` in memory.
3. **Line 217**: `pos.UpdateLastKnownBonded(bonded)` — writes updated bonded state in memory.
4. **Line 230**: `sufficientBonusPoolBalance` — if pool is empty, returns `ErrInsufficientBonusPool`. At this point all three checkpoint fields are already mutated in the caller's `pos`.

**`slashRedelegationPosition` (slash.go:54–77) swallows the error and persists the mutated `pos`:**

- Lines 56–63: `ErrInsufficientBonusPool` is caught, logged, and execution continues with the already-mutated `pos`.
- **Line 77** (partial slash path): `setPositionWithState(ctx, pos, nil)` — persists `pos` with the advanced `LastBonusAccrual`, `LastEventSeq`, and consumed event ref-counts, but zero bonus paid.

**Full-slash path is NOT affected**: lines 68–71 call `pos.ClearBonusCheckpoints()` which resets `LastBonusAccrual` to zero, so the full-slash case does not permanently lose the period.

**The partial-slash path is the vulnerable one.** The period `[old_LastBonusAccrual, slash_blockTime]` is permanently erased: events are consumed, the checkpoint is advanced, but no bonus is transferred.

---

### Title
Partial Redelegation Slash with Empty Bonus Pool Permanently Erases Accrued Bonus Rewards — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

### Summary
In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is intentionally swallowed to prevent chain halt. However, `processEventsAndClaimBonus` mutates the position's bonus checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) and decrements validator event reference counts **before** performing the pool balance check. The caller then persists the mutated position via `setPositionWithState`. The result is that the accrued bonus for the period up to the slash is permanently lost: the checkpoint advances, the events are consumed, but no coins are transferred to the owner.

### Finding Description

`processEventsAndClaimBonus` takes `pos *types.PositionState` by pointer and mutates it unconditionally before the pool check: [1](#0-0) [2](#0-1) 

The pool sufficiency check comes after: [3](#0-2) 

When `ErrInsufficientBonusPool` is returned, `slashRedelegationPosition` swallows it: [4](#0-3) 

Then, for a partial slash, the already-mutated `pos` (with advanced `LastBonusAccrual` and consumed `LastEventSeq`) is persisted: [5](#0-4) 

The full-slash branch at lines 68–71 is not affected because `ClearBonusCheckpoints()` resets the state regardless: [6](#0-5) 

### Impact Explanation

The position owner permanently loses all bonus rewards accrued from `pos.LastBonusAccrual` up to the slash block time. The amount is `shares × tokensPerShare × bonusApy × durationSeconds / SecondsPerYear`. After the slash, the checkpoint is at `blockTime` and the validator events for that period have had their reference counts decremented (potentially garbage-collected). No future `claimRewards` call can recover this period. This is a direct, irreversible fund loss for the position owner.

### Likelihood Explanation

The preconditions are:
1. A tier position has an active redelegation (created via `MsgTierRedelegate`).
2. The destination validator is partially slashed while the redelegation is still in the unbonding period (triggering `BeforeRedelegationSlashed`).
3. The `RewardsPoolName` module account balance is zero or below the accrued bonus amount at the time of the slash.

Condition 3 is realistic: the pool can be drained by the BeginBlocker base-rewards top-up, or simply not yet funded. Conditions 1 and 2 are normal protocol operations. No privileged access is required; the scenario occurs automatically through standard staking slash mechanics.

### Recommendation

Move `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` to execute **only after** a successful `sufficientBonusPoolBalance` check and `SendCoinsFromModuleToAccount`. Alternatively, in `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, explicitly restore the original checkpoint values on `pos` before calling `setPositionWithState`, so the period is not lost and can be claimed once the pool is replenished.

### Proof of Concept

```go
// Keeper integration test outline:
// 1. Setup a tier position and redelegate it to a second validator.
// 2. Advance block time by 30 days so bonus accrues.
// 3. Drain RewardsPoolName to zero.
// 4. Fire BeforeRedelegationSlashed with a partial sharesToUnbond.
// 5. Fund the pool.
// 6. Call ClaimTierRewards for the position.
// 7. Assert owner receives zero bonus — the 30-day period is permanently lost.
//    (LastBonusAccrual was advanced to the slash block time without payment.)
```

The existing test `TestSlashRedelegationPosition_ClaimsBonusRewardsUpToSlash` in `x/tieredrewards/keeper/slash_test.go` funds the pool before firing the hook. Running the same test without funding the pool and then funding it afterward and calling `ClaimTierRewards` would demonstrate zero payout for the slashed period. [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L193-193)
```go
		pos.UpdateLastEventSeq(entry.Seq)
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

**File:** x/tieredrewards/keeper/slash.go (L68-71)
```go
	if fullSlash {
		pos.Delegation = nil
		pos.ClearBonusCheckpoints()
		return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
```

**File:** x/tieredrewards/keeper/slash.go (L76-77)
```go
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** x/tieredrewards/keeper/slash_test.go (L63-101)
```go
// TestSlashRedelegationPosition_ClaimsBonusRewardsUpToSlash verifies that when
// a redelegation slash fires, any bonus accrued on the destination delegation
// since the last accrual checkpoint is paid out to the position owner, and the
// position's bonus-state checkpoints (LastBonusAccrual, LastKnownBonded) are
// advanced.
func (s *KeeperSuite) TestSlashRedelegationPosition_ClaimsBonusRewardsUpToSlash() {
	lockAmount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
	_, bondDenom := s.getStakingData()
	s.fundRewardsPool(sdkmath.NewInt(1_000_000_000), bondDenom)

	pos, _, unbondingID := s.setupRedelegatingPosition(lockAmount)
	owner := sdk.MustAccAddressFromBech32(pos.Owner)
	preAccrual := pos.LastBonusAccrual

	// Advance block time so bonus accrues on the destination validator.
	s.ctx = s.ctx.WithBlockHeight(s.ctx.BlockHeight() + 1)
	s.ctx = s.ctx.WithBlockTime(s.ctx.BlockTime().Add(30 * 24 * time.Hour))

	balBefore := s.app.BankKeeper.GetBalance(s.ctx, owner, bondDenom)

	// Partial slash — a small fraction of shares.
	sharesToUnbond := pos.Delegation.Shares.Quo(sdkmath.LegacyNewDec(10))
	err := s.keeper.Hooks().BeforeRedelegationSlashed(s.ctx, unbondingID, sharesToUnbond)
	s.Require().NoError(err)

	balAfter := s.app.BankKeeper.GetBalance(s.ctx, owner, bondDenom)
	s.Require().True(balAfter.Amount.GT(balBefore.Amount),
		"owner should have received bonus rewards accrued up to slash: before=%s after=%s",
		balBefore.Amount, balAfter.Amount)

	updated, err := s.keeper.GetPositionState(s.ctx, pos.Id)
	s.Require().NoError(err)
	s.Require().True(updated.LastBonusAccrual.After(preAccrual),
		"LastBonusAccrual should have advanced past the pre-slash checkpoint")
	s.Require().Equal(s.ctx.BlockTime(), updated.LastBonusAccrual,
		"LastBonusAccrual should advance to the slash block time")
	s.Require().True(updated.LastKnownBonded,
		"LastKnownBonded should remain true — destination validator is still bonded")
}
```
