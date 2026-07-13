The code path is fully traceable. Let me confirm the exact ordering in `processEventsAndClaimBonus` and the error-swallowing in `slashRedelegationPosition`.

**Exact execution trace:**

**Step 1 — `processEventsAndClaimBonus` (`claim_rewards.go`):**

- Lines 172–198: event loop runs, calling `pos.UpdateLastEventSeq(entry.Seq)` (line 193) and `k.decrementEventRefCount(ctx, valAddr, entry.Seq)` (line 196) for every event — both the in-memory checkpoint mutation and the store write happen here.
- Line 215: `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` — `LastBonusAccrual` advanced in-memory.
- Line 217: `pos.UpdateLastKnownBonded(bonded)` — in-memory.
- Line 230: `sufficientBonusPoolBalance` — only now is the pool checked. Returns `ErrInsufficientBonusPool` with `pos` already fully mutated and event ref-counts already decremented in the store.

**Step 2 — `slashRedelegationPosition` (`slash.go`):**

- Line 54: calls `processEventsAndClaimBonus(ctx, &pos)` by pointer.
- Lines 56–63: catches `ErrInsufficientBonusPool`, logs it, does NOT return — execution continues.
- Line 77 (partial slash path): calls `setPositionWithState(ctx, pos, nil)` — persists the already-mutated `pos` with advanced `LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`.

The invariant "checkpoints only advance when bonus is paid" is broken. The user's entire pre-slash bonded segment is permanently unclaimable.

---

### Title
Checkpoint Advancement Before Pool Check in `slashRedelegationPosition` Causes Permanent Bonus Loss on Insufficient Pool — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

### Summary

When `BeforeRedelegationSlashed` fires and `RewardsPoolName` holds less than the computed bonus, `processEventsAndClaimBonus` mutates `pos.LastEventSeq`, `pos.LastBonusAccrual`, and `pos.LastKnownBonded` in-memory and decrements event reference counts in the store **before** reaching `sufficientBonusPoolBalance`. The returned `ErrInsufficientBonusPool` is silently swallowed in `slashRedelegationPosition`, and `setPositionWithState` then persists the advanced checkpoints. The user permanently loses the bonus for the entire pre-slash bonded segment with no retry path.

### Finding Description

`processEventsAndClaimBonus` in `claim_rewards.go` performs all checkpoint mutations and store writes before the pool-balance guard: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

By the time `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` at line 230, the following have already occurred:

- `pos.LastEventSeq` advanced to the latest processed event seq (line 193).
- All processed events have had their `ReferenceCount` decremented in the store (line 196), potentially garbage-collecting them.
- `pos.LastBonusAccrual` advanced to `blockTime` (line 215).
- `pos.LastKnownBonded` updated (line 217).

`slashRedelegationPosition` swallows the error and unconditionally calls `setPositionWithState`: [5](#0-4) [6](#0-5) 

For a **partial slash**, the position remains delegated with the advanced checkpoints persisted. Any subsequent `ClaimTierRewards` call will compute bonus only from the new (advanced) `LastBonusAccrual`, skipping the entire pre-slash bonded segment. For a **full slash**, `ClearBonusCheckpoints()` resets the checkpoints, but the bonus is still never paid.

The ADR documents "Bonus forfeits silently if the pool is insufficient (chain-halt avoidance)" in section 6, but the implementation goes further than a simple forfeit: it also permanently advances the position's replay cursors, making the forfeited segment irrecoverable even after the pool is replenished. [7](#0-6) 

### Impact Explanation

A position owner who has accrued bonus over a long bonded segment (e.g., months at a high APY on a large stake) loses that entire accrued bonus permanently if the rewards pool is depleted at the moment their redelegation entry is slashed. The pool balance is a shared resource that can be legitimately depleted by normal claim activity. After the slash hook fires, the position's `LastBonusAccrual` is set to the slash block time; all future claims start from that point, and the pre-slash segment is gone. The pool balance is unchanged (no payment was made), but the user's entitlement is erased.

### Likelihood Explanation

The `BeforeRedelegationSlashed` hook fires automatically during any double-sign slash that touches a redelegation entry — no user action is required. The rewards pool can be depleted by normal claim volume or by a governance delay in replenishment. The combination is realistic in production: a validator double-signs while the pool is temporarily empty or underfunded. No privileged access is required; the trigger is a standard Cosmos SDK staking slash event.

### Recommendation

Move `sufficientBonusPoolBalance` **before** any checkpoint mutation or store write inside `processEventsAndClaimBonus`. If the pool check fails, return the error without touching `pos.LastEventSeq`, `pos.LastBonusAccrual`, `pos.LastKnownBonded`, or any event reference counts. Alternatively, in `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, explicitly roll back the in-memory checkpoint fields on `pos` to their pre-call values before calling `setPositionWithState`, so the position retains its original cursors and the user can retry after the pool is replenished.

### Proof of Concept

```go
// Keeper test (unmodified Go/Cosmos test setup):
// 1. Create a tier position and let 30 days of bonus accrue.
// 2. Fund the rewards pool with 0 tokens (or drain it to below the computed bonus).
// 3. Set up a redelegation mapping for the position's unbonding ID.
// 4. Call BeforeRedelegationSlashed with a partial sharesToUnbond.
// 5. Assert: hook returns nil (no error).
// 6. Read the updated position: LastBonusAccrual == blockTime (advanced).
// 7. Assert: owner's bank balance is unchanged (no bonus paid).
// 8. Replenish the pool.
// 9. Call ClaimTierRewards for the position.
// 10. Assert: returned bonus is zero (pre-slash segment is permanently lost).
// 11. Assert: pool balance equals the replenished amount (nothing was ever paid).
```

The test mirrors the existing `TestSlashRedelegationPosition_ClaimsBonusRewardsUpToSlash` in `slash_test.go` but with `fundRewardsPool(0, bondDenom)` instead of `fundRewardsPool(1_000_000_000, bondDenom)`. [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L193-193)
```go
		pos.UpdateLastEventSeq(entry.Seq)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L196-198)
```go
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

**File:** x/tieredrewards/keeper/slash.go (L76-77)
```go
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** doc/architecture/adr-006.md (L310-310)
```markdown
| **BeforeRedelegationSlashed** | Fires before staking's `Unbond` in `SlashRedelegation`. Routes via `RedelegationMappings[unbondingId]` to the affected position and runs `processEventsAndClaimBonus` against **pre-slash** shares. Base rewards auto-withdraw inside distribution's `BeforeDelegationSharesModified` (still fired by the subsequent `Unbond`). On full slash, `pos.Delegation` is set to nil and checkpoints reset. Bonus forfeits silently if the pool is insufficient (chain-halt avoidance). |
```

**File:** x/tieredrewards/keeper/slash_test.go (L68-101)
```go
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
