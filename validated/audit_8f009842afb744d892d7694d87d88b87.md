### Title
Mandatory `claimRewards` in every exit path permanently blocks principal withdrawal when the bonus pool is depleted — (`x/tieredrewards/keeper/msg_server.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

Every user-callable exit path in `x/tieredrewards` — `MsgTierUndelegate`, `MsgExitTierWithDelegation`, and `MsgTierRedelegate` — mandatorily calls `claimRewards` before executing. `claimRewards` calls `processEventsAndClaimBonus`, which calls `sufficientBonusPoolBalance` and fails atomically if the bonus pool cannot cover accrued bonus. Because all exit paths share this mandatory check, a depleted bonus pool permanently blocks every route by which a user can recover their locked principal, not just their bonus rewards.

---

### Finding Description

`MsgTierUndelegate`, `MsgExitTierWithDelegation`, and `MsgTierRedelegate` each call `claimRewards` unconditionally before performing any state mutation:

```go
// msg_server.go – TierUndelegate
pos, _, _, err = ms.claimRewards(ctx, pos)
if err != nil {
    return nil, err
}
``` [1](#0-0) 

The same pattern appears in `ExitTierWithDelegation`:

```go
pos, _, _, err = ms.claimRewards(ctx, pos)
if err != nil {
    return nil, err
}
``` [2](#0-1) 

And in `TierRedelegate`: [3](#0-2) 

`claimRewards` calls `processEventsAndClaimBonus`, which checks pool sufficiency and fails hard:

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err
}
``` [4](#0-3) 

`sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` whenever the pool balance is less than the accrued bonus:

```go
if !poolBalance.IsAllGTE(bonus) {
    return errorsmod.Wrapf(types.ErrInsufficientBonusPool, ...)
}
``` [5](#0-4) 

The only messages that do **not** call `claimRewards` are `TriggerExitFromTier` (sets timestamps only) and `WithdrawFromTier` (requires the position to already be undelegated). `WithdrawFromTier` is therefore unreachable if `TierUndelegate` is blocked. [6](#0-5) 

The ADR documents this as a known design choice for user-driven paths:

> "Pool empty (user-driven) | Message fails atomically. No state change. Retry after pool replenished." [7](#0-6) 

However, the documentation frames this as a temporary inconvenience for reward claims, not as a risk to the user's **principal**. There is no emergency exit path that forfeits accrued bonus and returns only the principal.

---

### Impact Explanation

When the bonus pool is depleted and a user has accrued non-zero bonus rewards, the user's locked principal (the tokens delegated via `MsgLockTier` or `MsgCommitDelegationToTier`) becomes inaccessible. All three exit paths fail with `ErrInsufficientBonusPool`:

- `MsgTierUndelegate` → blocked → `MsgWithdrawFromTier` is unreachable
- `MsgExitTierWithDelegation` → blocked
- `MsgTierRedelegate` → blocked (cannot move to a different validator)

The user cannot recover their principal until governance replenishes the pool. The corrupted invariant is: **after the exit commitment period elapses, a user must always be able to recover their principal**. The bonus pool state — a shared resource entirely outside the user's control — can permanently violate this invariant.

This is structurally identical to the BalancedVault M-16 finding: a shared resource failure (broken market / empty pool) blocks all withdrawal paths, including recovery of funds deposited to healthy components.

---

### Likelihood Explanation

The bonus pool is a finite module account funded by governance. It depletes naturally as users claim rewards over time. No attacker action is required: the pool will eventually run dry if governance does not replenish it on schedule. Any user who has held a position long enough to accrue non-zero bonus is affected the moment the pool balance drops below their accrued amount. The `BeginBlocker` top-up mechanism only covers base rewards, not the bonus pool directly. [8](#0-7) 

---

### Recommendation

Implement an emergency exit path (e.g., `MsgEmergencyExitTier`) that skips bonus reward settlement and transfers only the principal back to the owner, forfeiting any accrued but unpayable bonus. Alternatively, modify `TierUndelegate` and `ExitTierWithDelegation` to silently skip bonus payment (logging the forfeiture) when the pool is insufficient, mirroring the existing `BeforeRedelegationSlashed` hook behavior:

```go
// slash.go – existing precedent for silent bonus forfeit
if errors.Is(err, types.ErrInsufficientBonusPool) {
    k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
} else {
    return err
}
``` [9](#0-8) 

At minimum, the user-facing documentation must clearly state that a depleted bonus pool blocks principal withdrawal, not just reward claims.

---

### Proof of Concept

1. Alice locks tokens via `MsgLockTier` and delegates to validator V. Her position accrues bonus rewards over time.
2. The bonus pool is depleted (naturally, as other users claim rewards).
3. Alice's exit commitment period elapses. She sends `MsgTriggerExitFromTier` — succeeds (no `claimRewards` call).
4. Alice sends `MsgTierUndelegate` to begin the withdrawal path. The call reaches `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance`. Pool balance < accrued bonus → `ErrInsufficientBonusPool` → transaction reverts.
5. Alice sends `MsgExitTierWithDelegation` — same failure.
6. Alice sends `MsgTierRedelegate` — same failure.
7. Alice's principal remains locked in the position's delegator address. `MsgWithdrawFromTier` is unreachable because the position is still delegated.

The unit test `TestMsgClaimTierRewards_FailsWhenBonusPoolInsufficient` confirms the atomic failure behavior: [10](#0-9) 

The same `ErrInsufficientBonusPool` propagates through `TierUndelegate` and `ExitTierWithDelegation` via the shared `claimRewards` call path, blocking all principal recovery.

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L166-169)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L229-232)
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
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

**File:** x/tieredrewards/keeper/msg_validate.go (L183-210)
```go
func (k Keeper) validateWithdrawFromTier(ctx context.Context, pos types.PositionState, owner string) error {
	if !pos.IsOwner(owner) {
		return types.ErrNotPositionOwner
	}

	if !pos.HasTriggeredExit() {
		return types.ErrExitNotTriggered
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if !pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationNotReached
	}

	if pos.IsDelegated() {
		return types.ErrPositionDelegated
	}

	isUnbonding, err := k.isUnbonding(ctx, pos.DelegatorAddress)
	if err != nil {
		return err
	}
	if isUnbonding {
		return types.ErrPositionUnbonding
	}

	return nil
}
```

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

```

**File:** doc/architecture/adr-006.md (L331-331)
```markdown
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

**File:** x/tieredrewards/keeper/msg_server_claim_rewards_test.go (L100-133)
```go
// TestMsgClaimTierRewards_FailsWhenBonusPoolInsufficient verifies that ClaimTierRewards
// returns ErrInsufficientBonusPool when accrued bonus cannot be paid, so the tx rolls
// back and the user can retry later without losing base rewards to a partial claim.
func (s *KeeperSuite) TestMsgClaimTierRewards_FailsWhenBonusPoolInsufficient() {
	lockAmount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
	pos := s.setupNewTierPosition(lockAmount, false)
	delAddr := sdk.MustAccAddressFromBech32(pos.Owner)
	valAddr := sdk.MustValAddressFromBech32(pos.Delegation.ValidatorAddress)
	_, bondDenom := s.getStakingData()
	msgServer := keeper.NewMsgServerImpl(s.keeper)

	s.setValidatorCommission(valAddr, sdkmath.LegacyZeroDec())

	// Advance time and allocate base rewards, but intentionally leave bonus pool empty.
	s.ctx = s.ctx.WithBlockHeight(s.ctx.BlockHeight() + 1)
	s.ctx = s.ctx.WithBlockTime(s.ctx.BlockTime().Add(time.Hour * 24 * 365))
	s.allocateRewardsToValidator(valAddr, sdkmath.NewInt(100), bondDenom)

	// Bonus pool remains at 0 — bonus accrued but pool cannot cover it.
	balBefore := s.app.BankKeeper.GetBalance(s.ctx, delAddr, bondDenom)

	// Use a branched context so a failed message does not persist state (matches DeliverTx rollback).
	cacheCtx, _ := s.ctx.CacheContext()
	resp, err := msgServer.ClaimTierRewards(cacheCtx, &types.MsgClaimTierRewards{
		Owner:       delAddr.String(),
		PositionIds: []uint64{pos.Id},
	})
	s.Require().Error(err)
	s.Require().True(errors.Is(err, types.ErrInsufficientBonusPool))
	s.Require().Nil(resp)

	balAfter := s.app.BankKeeper.GetBalance(s.ctx, delAddr, bondDenom)
	s.Require().True(balAfter.Amount.Equal(balBefore.Amount), "failed claim must not transfer rewards")
}
```
