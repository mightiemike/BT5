### Title
Empty Bonus Pool Blocks All Exit Paths from Tier Positions, Locking User Funds — (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

In the `x/tieredrewards` module, both exit paths — `MsgTierUndelegate` and `MsgExitTierWithDelegation` — unconditionally settle accrued bonus rewards before performing the actual undelegation or delegation transfer. If the shared `RewardsPoolName` module account has insufficient balance to cover the accrued bonus, both handlers fail atomically with `ErrInsufficientBonusPool`. This permanently blocks users from withdrawing their locked principal until an external party replenishes the pool, regardless of how long the user has waited through their exit commitment period.

---

### Finding Description

`MsgTierUndelegate` at `x/tieredrewards/keeper/msg_server.go:166` calls `claimRewards` before performing any undelegation:

```go
pos, _, _, err = ms.claimRewards(ctx, pos)
if err != nil {
    return nil, err
}
``` [1](#0-0) 

`claimRewards` calls `processEventsAndClaimBonus`:

```go
bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
if err != nil {
    return types.PositionState{}, nil, nil, err
}
``` [2](#0-1) 

`processEventsAndClaimBonus` calls `sufficientBonusPoolBalance` before sending any coins:

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err
}
``` [3](#0-2) 

`sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` if the pool cannot cover the accrued bonus:

```go
if !poolBalance.IsAllGTE(bonus) {
    return errorsmod.Wrapf(types.ErrInsufficientBonusPool, ...)
}
``` [4](#0-3) 

The same `claimRewards` call is present in `MsgExitTierWithDelegation` (the instant, no-unbonding exit path), as documented in the ADR:

> "MsgExitTierWithDelegation: Claim rewards for position (settle base + bonus)" [5](#0-4) 

The pool is the same `RewardsPoolName` module account that the `BeginBlocker` (`topUpBaseRewards`) drains every block to top up base staking rewards:

```go
err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, ...)
``` [6](#0-5) 

The design explicitly acknowledges this behavior:

> "User-driven paths (ClaimTierRewards, AddToPosition, **Undelegate**, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished." [7](#0-6) 

The pool has no guaranteed replenishment mechanism — it is funded by external bank sends to the module address. There is no on-chain obligation for anyone to refill it.

---

### Impact Explanation

A user who has locked tokens in a tier position, triggered exit, and waited the full exit commitment period (e.g., 1 year) is entitled to withdraw their principal. However, if the bonus pool is empty or insufficient at the moment they call `MsgTierUndelegate` or `MsgExitTierWithDelegation`, both calls fail. The user's principal is locked inside the tier module's per-position delegator account indefinitely. There is no alternative exit path that bypasses the mandatory reward settlement. The corrupted invariant is: **a user who has completed their exit commitment is entitled to recover their principal, but the module prevents this recovery based on the state of an unrelated shared pool**.

---

### Likelihood Explanation

The `BeginBlocker` runs every block and continuously drains the pool for base rewards top-up whenever the fee collector falls short of `TargetBaseRewardsRate`. [8](#0-7) 

In a scenario where the pool is not actively replenished — for example, if the project's operational funding lapses, or if a large number of users simultaneously claim bonus rewards — the pool reaches zero. At that point, every user with accrued bonus (i.e., every delegated position that has been active for any non-zero time) is blocked from undelegating or exiting. The longer the pool remains empty, the more bonus accrues, making the required replenishment amount grow over time and further delaying recovery.

---

### Recommendation

Decouple reward settlement from the exit/undelegation flow. `MsgTierUndelegate` and `MsgExitTierWithDelegation` should advance the bonus accrual checkpoint and record the owed bonus as a claimable debt without requiring immediate payment from the pool. The user can then claim the owed bonus separately once the pool is replenished. The principal withdrawal must never be gated on the pool's solvency.

---

### Proof of Concept

1. User calls `MsgLockTier` to lock 10,000 tokens in Tier 1 (1-year exit commitment).
2. User calls `MsgTriggerExitFromTier` immediately.
3. User waits 1 year for `ExitUnlockAt` to elapse.
4. During this year, `BeginBlocker.topUpBaseRewards` runs every block, continuously draining the pool.
5. Pool balance reaches zero (or below the user's accrued bonus).
6. User calls `MsgTierUndelegate` → `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` → transaction reverts.
7. User calls `MsgExitTierWithDelegation` → same failure path.
8. User's 10,000 tokens remain locked in the tier module's delegator account. No exit path exists until an external party sends tokens to the `RewardsPoolName` module account. [9](#0-8) [10](#0-9)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L152-207)
```go
func (ms msgServer) TierUndelegate(ctx context.Context, msg *types.MsgTierUndelegate) (*types.MsgTierUndelegateResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateUndelegatePosition(ctx, pos, msg.Owner); err != nil {
		return nil, err
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}

	srcValidator := pos.Delegation.ValidatorAddress
	valAddr, err := sdk.ValAddressFromBech32(srcValidator)
	if err != nil {
		return nil, err
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	completionTime, _, err := ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
	}

	pos.ClearBonusCheckpoints()

	if err := ms.setPosition(ctx, pos.Position, &ValidatorTransition{PreviousAddress: srcValidator}); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionUndelegated{
		PositionId:     pos.Id,
		TierId:         pos.TierId,
		Owner:          pos.Owner,
		Validator:      srcValidator,
		CompletionTime: completionTime,
	}); err != nil {
		return nil, err
	}

	return &types.MsgTierUndelegateResponse{
		CompletionTime: completionTime,
		PositionId:     pos.Id,
	}, nil
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L97-100)
```go
	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
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

**File:** doc/architecture/adr-006.md (L232-250)
```markdown
### MsgExitTierWithDelegation Flow

```
-> Validate: owner match, delegated, exit triggered, exit elapsed, amount > 0, amount <= position amount, no active redelegation
-> Claim rewards for position (settle base + bonus)
-> positionAmount = TokensFromShares(pos.Delegation.Shares)  // pre-transfer live value
-> If amount == positionAmount (full exit): unbondedShares = pos.Delegation.Shares
   Else (partial): unbondedShares = ValidateUnbondAmount(posDelAddr, valAddr, amount)
-> transferDelegationFromPosition: Unbond(posDelAddr, valAddr, unbondedShares) -> transferredAmount
   Re-fetch validator, Delegate(owner, transferredAmount, validator) — instant, no unbonding
-> If full exit:
     sweep the position's spendable bank balance (SpendableCoins, not GetAllBalances)
     from posDelAddr to owner.
     delete position (all indexes cleaned up, WithdrawAddr cleared via DeleteDelegatorWithdrawAddr)
   Else:
     remaining token value must meet tier.MinLockAmount (post-transfer check on actual amount)
     save position
-> Emit EventExitTierWithDelegation(position_id, tier_id, owner, validator, transferred_amount, transferred_shares, full_exit)
```
```

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

```

**File:** x/tieredrewards/keeper/abci.go (L96-111)
```go
	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetBalance(ctx, poolAddr, bondDenom)
	topUpAmount := shortFallAmount
	if poolBalance.Amount.IsZero() {
		k.logger(ctx).Error("base rewards pool is empty, cannot top up validator rewards",
			"shortfall", shortFallAmount.String(),
		)
		return nil
	}
	if poolBalance.Amount.LT(shortFallAmount) {
		k.logger(ctx).Error("base rewards pool has insufficient funds, distributing remaining balance",
			"shortfall", shortFallAmount.String(),
			"pool_balance", poolBalance.Amount.String(),
		)
		topUpAmount = poolBalance.Amount
	}
```

**File:** x/tieredrewards/keeper/abci.go (L113-116)
```go
	err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, sdk.NewCoins(sdk.NewCoin(bondDenom, topUpAmount)))
	if err != nil {
		return err
	}
```

**File:** x/tieredrewards/types/errors.go (L17-17)
```go
	ErrInsufficientBonusPool            = errors.Register(ModuleName, 12, "insufficient bonus pool balance")
```
