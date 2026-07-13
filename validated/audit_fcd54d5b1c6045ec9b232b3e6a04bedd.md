### Title
Insufficient Bonus Pool Blocks All Position Exit Paths, Locking Delegator Funds - (File: x/tieredrewards/keeper/claim_rewards.go)

### Summary

`processEventsAndClaimBonus` is a mandatory prerequisite for every position-mutation message in `x/tieredrewards`. When the bonus rewards pool balance falls below the accrued bonus for a position, `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`, causing the entire transaction to revert. Because `claimRewards` (which calls `processEventsAndClaimBonus`) is invoked unconditionally before every exit path — `MsgTierUndelegate`, `MsgExitTierWithDelegation`, `MsgTierRedelegate`, `MsgAddToTierPosition`, and `MsgClearPosition` — a depleted pool simultaneously blocks all routes by which a delegator can recover their locked principal.

### Finding Description

`processEventsAndClaimBonus` computes the accrued bonus and then calls `sufficientBonusPoolBalance` before transferring coins:

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err   // ErrInsufficientBonusPool propagates up
}
``` [1](#0-0) 

`sufficientBonusPoolBalance` returns an error whenever the pool cannot cover the full bonus:

```go
if !poolBalance.IsAllGTE(bonus) {
    return errorsmod.Wrapf(types.ErrInsufficientBonusPool, ...)
}
``` [2](#0-1) 

Every position-mutation handler calls `claimRewards` (which calls `processEventsAndClaimBonus`) before performing its state change:

- `TierRedelegate`: [3](#0-2) 
- `AddToTierPosition`: [4](#0-3) 
- `ExitTierWithDelegation`: [5](#0-4) 

The ADR explicitly documents this behavior: [6](#0-5) 

> **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

The design assumes the pool will always be replenished, but provides no on-chain guarantee of this. If the pool is depleted and governance or the pool funder fails to act, every delegated position is permanently inaccessible.

`MsgWithdrawFromTier` requires the position to already be undelegated, so it cannot serve as an alternative exit path when `MsgTierUndelegate` itself is blocked. [7](#0-6) 

### Impact Explanation

When the `RewardsPoolName` module account balance drops below the accrued bonus of any delegated position, that position's owner cannot:

1. Undelegate (start the 21-day unbonding period to recover principal).
2. Exit with delegation (instant principal recovery on the same validator).
3. Redelegate to a different validator.
4. Add tokens to the position.
5. Clear a triggered exit.

The locked value is the delegator's full staked principal, not just the bonus. Because all exit paths share the same mandatory `claimRewards` → `processEventsAndClaimBonus` → pool-balance check, a single pool-depletion event simultaneously locks every delegated position on the chain. Recovery depends entirely on an off-chain actor (governance or pool funder) replenishing the pool; there is no on-chain fallback. [8](#0-7) 

### Likelihood Explanation

The bonus pool is an externally funded module account. It is drained continuously by normal reward claims. Any of the following realistic conditions depletes it:

- The pool was seeded with a finite budget that is exhausted over time.
- A governance proposal to top up the pool fails or is delayed.
- A large number of positions claim simultaneously (e.g., triggered by a `MsgUpdateTier` APY change, which force-settles all positions in the tier).

The `MsgUpdateTier` APY-change path calls `claimRewardsAndUpdateTierPositions` for every position in the tier and fails atomically if the pool is insufficient, as confirmed by the test `TestUpdateTier_BonusApyChange_InsufficientPool`. [9](#0-8) 

No privileged access is required to trigger the condition; it arises from normal protocol operation.

### Recommendation

Decouple the bonus payment from the exit/undelegate paths. Specifically:

1. Allow `MsgTierUndelegate` and `MsgExitTierWithDelegation` to proceed even when the pool cannot cover the accrued bonus. Record the unpaid bonus as a debt on the position (or forfeit it with an event), and advance the accrual checkpoint so the position is not re-charged on the next attempt.
2. Alternatively, only block `MsgClaimTierRewards` on pool insufficiency; let structural mutations (undelegate, exit) skip the bonus transfer and emit an event indicating forfeited bonus.

The `BeforeRedelegationSlashed` hook already implements the correct pattern — it silently forfeits bonus when the pool is insufficient to avoid a chain halt: [10](#0-9) 

The same "forfeit-on-empty" logic should be applied to user-initiated exit paths.

### Proof of Concept

1. Alice locks tokens via `MsgLockTier`, creating a delegated position with accrued bonus.
2. The `RewardsPoolName` module account balance is zero (pool depleted by prior claims or never funded).
3. Alice's exit lock duration elapses. She submits `MsgTierUndelegate`.
4. The handler calls `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`.
5. The transaction reverts. Alice's principal remains locked in the tier module.
6. Alice submits `MsgExitTierWithDelegation` — same failure.
7. Alice submits `MsgTierRedelegate` — same failure.
8. `MsgWithdrawFromTier` is unavailable because the position is still delegated (step 4 never completed).
9. Alice's principal is inaccessible until an off-chain actor replenishes the pool. [11](#0-10) [12](#0-11)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L219-232)
```go
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

**File:** x/tieredrewards/keeper/bonus_rewards.go (L55-59)
```go
	if !poolBalance.IsAllGTE(bonus) {
		return errorsmod.Wrapf(types.ErrInsufficientBonusPool,
			"bonus: %s, pool balance: %s",
			bonus.String(), poolBalance.String())
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L229-232)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L314-317)
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

**File:** doc/architecture/adr-006.md (L164-164)
```markdown
| **MsgWithdrawFromTier** | Withdraw tokens + delete position. | Exit triggered; exit elapsed; not delegated; no pending unbonding |
```

**File:** doc/architecture/adr-006.md (L293-294)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.
```

**File:** x/tieredrewards/keeper/msg_server_auth_test.go (L339-365)
```go
func (s *KeeperSuite) TestUpdateTier_BonusApyChange_InsufficientPool() {
	lockAmount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
	pos := s.setupNewTierPosition(lockAmount, false)
	valAddr := sdk.MustValAddressFromBech32(pos.Delegation.ValidatorAddress)

	s.setValidatorCommission(valAddr, sdkmath.LegacyZeroDec())

	// Advance time so bonus accrues.
	s.ctx = s.ctx.WithBlockHeight(s.ctx.BlockHeight() + 1)
	s.ctx = s.ctx.WithBlockTime(s.ctx.BlockTime().Add(30 * 24 * time.Hour))

	// Update tier with new BonusApy — should fail due to insufficient pool.
	tier := newTestTier(1)
	initialBonusApy := tier.BonusApy
	updatedBonusApy := sdkmath.LegacyNewDecWithPrec(8, 2)
	tier.BonusApy = updatedBonusApy
	msgServer := keeper.NewMsgServerImpl(s.keeper)
	_, err := msgServer.UpdateTier(s.ctx, &types.MsgUpdateTier{
		Authority: s.keeper.GetAuthority(),
		Tier:      tier,
	})
	s.Require().ErrorIs(err, types.ErrInsufficientBonusPool)

	// Tier should NOT have been updated (tx failed).
	got, err := s.keeper.GetTier(s.ctx, 1)
	s.Require().NoError(err)
	s.Require().True(initialBonusApy.Equal(got.BonusApy), "tier should still have old APY")
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

**File:** x/tieredrewards/types/errors.go (L17-17)
```go
	ErrInsufficientBonusPool            = errors.Register(ModuleName, 12, "insufficient bonus pool balance")
```
