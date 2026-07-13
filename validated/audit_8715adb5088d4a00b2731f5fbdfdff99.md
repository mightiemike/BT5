### Title
Bonus Pool Depletion Permanently Traps Tier Position Principal — (`x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

When the `RewardsPoolName` module account is depleted, every exit path for a tier position (`TierUndelegate`, `TierRedelegate`, `ExitTierWithDelegation`) fails unconditionally because each one calls `claimRewards` before executing the exit. `claimRewards` calls `processEventsAndClaimBonus`, which hard-errors with `ErrInsufficientBonusPool` when the pool cannot cover accrued bonus. The error propagates and aborts the transaction, leaving users' locked principal permanently trapped. The developers already recognized this failure mode in the slash path and deliberately swallowed the error there, but the same guard is absent from all user-facing exit messages.

---

### Finding Description

**Entry paths that are blocked:**

`TierUndelegate` → `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` → **error** [1](#0-0) 

`TierRedelegate` → `claimRewards` → same chain [2](#0-1) 

`ExitTierWithDelegation` → `claimRewards` → same chain [3](#0-2) 

**The hard-error site:**

`processEventsAndClaimBonus` computes `totalBonus` and, if non-zero, calls `sufficientBonusPoolBalance`. If the pool balance is less than the owed bonus, it returns `ErrInsufficientBonusPool` — which propagates all the way back to the message handler, aborting the transaction. [4](#0-3) 

`sufficientBonusPoolBalance` compares the live pool balance against the owed coins and returns an error on shortfall: [5](#0-4) 

**The developer-acknowledged bypass that was never applied to exit paths:**

In `slashRedelegationPosition`, the same `ErrInsufficientBonusPool` is explicitly caught and swallowed so the slash can proceed without a chain halt. The comment reads *"Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt."* The identical logic is absent from `TierUndelegate`, `TierRedelegate`, and `ExitTierWithDelegation`. [6](#0-5) 

**Why principal is trapped:**

`lockFunds` sends the user's bond-denom tokens to the position's delegator sub-account, which then delegates them to a validator. The only way to recover those tokens is through `TierUndelegate` (unbonding path) or `ExitTierWithDelegation` (direct transfer path). Both are gated behind `claimRewards`. There is no code path that allows undelegation without first successfully claiming bonus rewards. [7](#0-6) 

---

### Impact Explanation

Any tier position that has accrued a non-zero bonus (i.e., any position that has been active for any duration under a non-zero `BonusApy` tier) becomes permanently unexitable once the `RewardsPoolName` module account balance falls below the owed amount. The user's locked principal — bond-denom tokens delegated through the position's sub-account — cannot be recovered. This is a direct analog to the M06 "run on the bank" scenario: the last position holders to attempt exit find the pool insolvent and their funds frozen.

The `BeginBlocker` path (`claimRewardsAndUpdateTierPositions`) also calls `processEventsAndClaimBonus` without the slash-path guard, meaning a depleted pool can additionally cause a chain halt at the start of every block. [8](#0-7) 

---

### Likelihood Explanation

The `RewardsPoolName` module account is a finite pool. Bonus rewards accrue continuously for every delegated position at the tier's `BonusApy` rate, computed as:

```
shares × tokensPerShare × BonusApy × durationSeconds / SecondsPerYear
``` [9](#0-8) 

If the pool is not replenished at a rate matching total accrual across all positions, it will eventually be depleted. This is especially likely when: (1) many positions are created in high-APY tiers, (2) the inflation funding mechanism is insufficient or paused, or (3) the pool is underfunded at genesis. No on-chain mechanism visible in the reviewed code prevents the pool from reaching zero while positions still hold accrued bonus debt.

---

### Recommendation

Decouple bonus reward claiming from the exit/undelegate operations. When `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool` inside `TierUndelegate`, `TierRedelegate`, and `ExitTierWithDelegation`, apply the same pattern already used in `slashRedelegationPosition`: log the shortfall, skip the bonus payment, and allow the exit to proceed. This ensures users can always recover their principal regardless of pool solvency, consistent with the design intent already expressed in the slash handler. [6](#0-5) 

---

### Proof of Concept

1. Governance creates a tier with non-zero `BonusApy` and a non-zero `MinLockAmount`.
2. Many users call `LockTier` or `CommitDelegationToTier`, creating positions that begin accruing bonus rewards.
3. Early users call `ClaimTierRewards` repeatedly, draining the `RewardsPoolName` module account.
4. The pool balance drops below the total accrued-but-unclaimed bonus owed to remaining positions.
5. Any remaining position holder calls `TierUndelegate` (or `TierRedelegate` / `ExitTierWithDelegation`).
6. The call chain reaches `sufficientBonusPoolBalance` at `claim_rewards.go:230`, which returns `ErrInsufficientBonusPool`.
7. The transaction is aborted; the user's bond-denom principal remains locked in the position's delegator sub-account with no available exit path.
8. The `BeginBlocker` also begins failing at `claimRewardsAndUpdateTierPositions`, escalating to a chain halt. [10](#0-9) [11](#0-10)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L50-80)
```go
func (k Keeper) claimRewardsAndUpdateTierPositions(ctx context.Context, tierId uint32) error {
	ids, err := k.getPositionsIdsByTier(ctx, tierId)
	if err != nil {
		return err
	}
	if len(ids) == 0 {
		return nil
	}

	for _, id := range ids {
		pos, err := k.getPositionState(ctx, id)
		if err != nil {
			return err
		}
		if !pos.IsDelegated() {
			continue
		}

		if _, err := k.claimBaseRewards(ctx, pos); err != nil {
			return err
		}
		if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
			return err
		}
		if err := k.setPosition(ctx, pos.Position, nil); err != nil {
			return err
		}
	}

	return nil
}
```

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L219-241)
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

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L25-46)
```go
func (k Keeper) computeSegmentBonus(pos types.PositionState, tier types.Tier, segmentStart, segmentEnd time.Time, tokensPerShare math.LegacyDec) math.Int {
	if !pos.ExitUnlockAt.IsZero() && segmentEnd.After(pos.ExitUnlockAt) {
		segmentEnd = pos.ExitUnlockAt
	}

	if !segmentEnd.After(segmentStart) {
		return math.ZeroInt()
	}

	durationSeconds := int64(segmentEnd.Sub(segmentStart) / time.Second)
	if durationSeconds <= 0 {
		return math.ZeroInt()
	}

	tokens := pos.Delegation.Shares.Mul(tokensPerShare)

	return tokens.
		Mul(tier.BonusApy).
		MulInt64(durationSeconds).
		QuoInt64(types.SecondsPerYear).
		TruncateInt()
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

**File:** x/tieredrewards/keeper/position.go (L74-81)
```go
// lockFunds locks the desired amount of funds into a position.
func (k Keeper) lockFunds(ctx context.Context, ownerAddr, delAddr sdk.AccAddress, amount math.Int) error {
	bondDenom, err := k.stakingKeeper.BondDenom(ctx)
	if err != nil {
		return err
	}
	return k.bankKeeper.SendCoins(ctx, ownerAddr, delAddr, sdk.NewCoins(sdk.NewCoin(bondDenom, amount)))
}
```
