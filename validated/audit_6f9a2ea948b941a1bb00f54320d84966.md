### Title
Insufficient Bonus Pool Permanently Blocks All Exit Paths for Tier Positions - (`x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

Every mutation message in `x/tieredrewards` that modifies a delegated position — `MsgTierUndelegate`, `MsgExitTierWithDelegation`, `MsgTierRedelegate`, `MsgClearPosition`, and `MsgAddToTierPosition` — mandatorily calls `claimRewards` before performing its state change. `claimRewards` calls `processEventsAndClaimBonus`, which hard-fails with `ErrInsufficientBonusPool` if the rewards pool cannot cover accrued bonus. Because there is no exit path that bypasses the bonus settlement step, an empty or underfunded bonus pool permanently blocks all exit operations, locking user principal (staked tokens) in the module's per-position delegator addresses until an external actor (governance) replenishes the pool.

---

### Finding Description

`claimRewards` is a mandatory prerequisite for every position-mutating message: [1](#0-0) 

Inside `claimRewards`, `processEventsAndClaimBonus` computes accrued bonus and checks pool sufficiency: [2](#0-1) 

If the pool balance is less than the accrued bonus, `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`: [3](#0-2) 

This error propagates atomically through every exit path:

- `TierUndelegate` calls `claimRewards` at line 166 before starting unbonding: [4](#0-3) 
- `ExitTierWithDelegation` calls `claimRewards` at line 532 before transferring delegation: [5](#0-4) 

The ADR explicitly documents this as expected behavior for user-driven paths: [6](#0-5) 

However, this design creates a situation where the user's principal (staked tokens held at the position's delegator address) is held hostage by the bonus pool balance — a completely separate accounting concern. The user's locked tokens are not at risk of loss, but they are inaccessible for an indefinite period.

---

### Impact Explanation

When the bonus pool is empty or insufficient:

1. `MsgTierUndelegate` fails — the user cannot start the 21-day unbonding period.
2. `MsgExitTierWithDelegation` fails — the user cannot instantly transfer their delegation back.
3. `MsgTierRedelegate` fails — the user cannot move to a safer validator.
4. `MsgClearPosition` fails — the user cannot cancel a triggered exit.

`MsgWithdrawFromTier` (the final withdrawal step) is only reachable after `TierUndelegate` completes, so it is also indirectly blocked. `MsgTriggerExitFromTier` still works, but it only starts the exit clock — it does not release funds.

The result is that a user who has completed their exit commitment period (i.e., `block_time >= ExitUnlockAt`) and is entitled to exit cannot do so. Their staked tokens remain locked in the module's per-position delegator address until governance replenishes the pool. [7](#0-6) 

---

### Likelihood Explanation

The bonus pool (`RewardsPoolName` module account) is a finite external resource. It can be drained by:

1. **BeginBlocker base-rewards top-up**: Every block, `BeginBlocker` transfers shortfall from the pool to the distribution module. Under high staking activity or a high `TargetBaseRewardsRate`, this continuously drains the pool. [8](#0-7) 
2. **Mass bonus claims**: Many users claiming rewards simultaneously can exhaust the pool.
3. **Governance inaction**: If governance fails to replenish the pool (e.g., due to a governance attack, parameter misconfiguration, or simply running out of community funds), the pool stays empty indefinitely.

Once the pool is empty, every user with any accrued bonus (i.e., every delegated position that has been active for any nonzero time) is blocked from exiting. This is not a rare edge case — it is the normal state of any position that has been delegated for more than a few seconds.

---

### Recommendation

Decouple the exit path from the mandatory bonus settlement. Specifically:

- For `MsgTierUndelegate` and `MsgExitTierWithDelegation`, allow the exit to proceed even if the bonus pool is insufficient. Either:
  - Forfeit the accrued bonus silently (similar to how `BeforeRedelegationSlashed` already handles this: [9](#0-8) ), or
  - Record the owed bonus as a debt to be paid when the pool is replenished.
- Reserve the hard-fail behavior only for `MsgClaimTierRewards`, `MsgTierRedelegate`, `MsgClearPosition`, and `MsgAddToTierPosition`, where the user is not trying to exit.

This mirrors the fix recommended in the external report: decouple the accounting constraint from the action that releases user funds.

---

### Proof of Concept

1. User locks tokens via `MsgLockTier` and waits for the exit commitment period to elapse (`ExitUnlockAt` passes).
2. The bonus pool is drained to zero (e.g., by BeginBlocker top-up or mass claims).
3. User submits `MsgTierUndelegate` to begin unbonding.
4. `TierUndelegate` → `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`.
5. The transaction reverts. The user's staked tokens remain locked at the position's delegator address.
6. User submits `MsgExitTierWithDelegation` as an alternative — same failure.
7. User has no remaining exit path. Funds are locked until governance replenishes the pool.

The test `TestProcessEvents_InsufficientPool_Error` confirms the error is returned: [10](#0-9)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L87-103)
```go
func (k Keeper) claimRewards(ctx context.Context, pos types.PositionState) (types.PositionState, sdk.Coins, sdk.Coins, error) {
	if !pos.IsDelegated() {
		return pos, sdk.NewCoins(), sdk.NewCoins(), nil
	}

	base, err := k.claimBaseRewards(ctx, pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	return pos, base, bonus, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-232)
```go
	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L48-61)
```go
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
	if bonus.IsZero() {
		return nil
	}

	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetAllBalances(ctx, poolAddr)
	if !poolBalance.IsAllGTE(bonus) {
		return errorsmod.Wrapf(types.ErrInsufficientBonusPool,
			"bonus: %s, pool balance: %s",
			bonus.String(), poolBalance.String())
	}

	return nil
```

**File:** x/tieredrewards/keeper/msg_server.go (L166-169)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L532-535)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** doc/architecture/adr-006.md (L293-294)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.
```

**File:** doc/architecture/adr-006.md (L296-298)
```markdown
### BeginBlocker: Base Rewards Top-Up

If `TargetBaseRewardsRate > 0`, the BeginBlocker computes the target per-block staker reward, compares it to the fee collector balance (after community tax), and transfers any shortfall from the rewards pool to the distribution module, allocated pro-rata by validator power.
```

**File:** doc/architecture/adr-006.md (L329-331)
```markdown
| Case | Behavior |
|------|----------|
| Pool empty (user-driven) | Message fails atomically. No state change. Retry after pool replenished. |
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

**File:** x/tieredrewards/keeper/claim_rewards_test.go (L905-918)
```go
// TestProcessEvents_InsufficientPool_Error verifies that claiming without
// a funded pool returns ErrInsufficientBonusPool.
func (s *KeeperSuite) TestProcessEvents_InsufficientPool_Error() {
	lockAmount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
	pos := s.setupNewTierPosition(lockAmount, false)

	// Advance time so bonus would be non-zero. Do NOT fund the pool.
	s.ctx = s.ctx.WithBlockTime(s.ctx.BlockTime().Add(30 * 24 * time.Hour))

	_, err := s.keeper.ProcessEventsAndClaimBonus(s.ctx, &pos)
	s.Require().Error(err, "should fail when bonus pool is insufficient")
	s.Require().ErrorContains(err, "insufficient bonus pool",
		"error should mention insufficient bonus pool")
}
```
