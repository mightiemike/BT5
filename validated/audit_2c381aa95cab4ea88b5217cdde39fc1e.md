### Title
Shared `RewardsPoolName` Drained by `topUpBaseRewards` Blocks All Position Exits via `ErrInsufficientBonusPool` — (`x/tieredrewards/keeper/abci.go`, `claim_rewards.go`, `bonus_rewards.go`)

---

### Summary

The `RewardsPoolName` module account is the single funding source for both the base-rewards top-up mechanism (`topUpBaseRewards`) and the bonus-rewards payout (`processEventsAndClaimBonus`). When the fee collector is consistently empty, `topUpBaseRewards` transfers the entire pool balance to the distribution module each block until the pool reaches zero. Once drained, any position with accrued non-zero bonus rewards cannot exit: `ExitTierWithDelegation` calls `claimRewards` before transferring the delegation back to the owner, and `claimRewards` hard-fails with `ErrInsufficientBonusPool` when the pool is empty. The principal (delegation) remains permanently locked in the position's delegator account.

---

### Finding Description

**Pool draining path — `topUpBaseRewards`:**

When `feeCollectorBalance = 0` (no transactions in a block), `defaultStakersRewardPerBlock = 0`, so `shortFallAmount = targetStakersRewardPerBlock` (positive). The code then caps `topUpAmount` at `poolBalance.Amount` and transfers the entire remaining pool to the distribution module: [1](#0-0) [2](#0-1) 

This repeats every block. Once `poolBalance.Amount` reaches zero, the guard at line 99 returns `nil` (no-op), leaving the pool permanently at zero. [3](#0-2) 

**Exit blocked — `ExitTierWithDelegation`:**

`claimRewards` is called at line 532, **before** `transferDelegationFromPosition` at line 548. If `claimRewards` returns any error, the function returns immediately and the delegation is never transferred back to the owner. [4](#0-3) [5](#0-4) 

**Hard failure in `processEventsAndClaimBonus`:**

When `totalBonus > 0` (position has accrued bonus over time with non-zero `BonusApy`), `sufficientBonusPoolBalance` is called. With a drained pool it returns `ErrInsufficientBonusPool`: [6](#0-5) 

This error propagates through `claimRewards`: [7](#0-6) 

The same `claimRewards` call appears in `TierUndelegate` (line 166), `TierRedelegate` (line 229), `AddToTierPosition` (line 314), and `ClearPosition` (line 406) — all exit/management paths are blocked simultaneously. [8](#0-7) 

---

### Impact Explanation

All position owners whose positions have accrued any non-zero bonus rewards are permanently unable to recover their principal delegation. The delegation shares remain locked in the position's dedicated delegator account. There is no fallback exit path that bypasses `claimRewards`. The invariant — that pool depletion must not prevent principal recovery — is violated. [9](#0-8) 

---

### Likelihood Explanation

The condition is reachable on any production chain where:
- `TargetBaseRewardsRate` is non-zero (the module's core purpose)
- Fee activity is low relative to the target rate (common on new or low-usage chains)
- Positions have been delegated long enough to accrue non-zero bonus (`BonusApy > 0`, any non-zero duration)

No attacker action is required beyond waiting. The pool drains passively via `BeginBlocker` on every block with insufficient fees. The `RewardsPoolName` is finite by design.

---

### Recommendation

Decouple the two uses of `RewardsPoolName`, or make the bonus claim non-fatal on pool exhaustion. Specifically:

1. **Skip bonus payout gracefully**: In `processEventsAndClaimBonus`, when `sufficientBonusPoolBalance` fails, log the shortfall and return `(sdk.NewCoins(), nil)` instead of propagating the error. Record the unclaimed bonus as a debt to be paid when the pool is replenished.
2. **Separate pool accounts**: Use distinct module accounts for base-rewards top-up and bonus rewards so that `topUpBaseRewards` cannot deplete the bonus reserve.
3. **Cap `topUpBaseRewards` drawdown**: Reserve a minimum balance in `RewardsPoolName` sufficient to cover outstanding bonus obligations before transferring to distribution.

---

### Proof of Concept

```
1. Deploy chain with TargetBaseRewardsRate = 0.10 (10% APY), BonusApy > 0, RewardsPoolName funded with F tokens.
2. User calls LockTier / CommitDelegationToTier → position created, bonus accrual starts.
3. Submit N empty blocks (no transactions). Each block:
   - feeCollectorBalance = 0
   - shortFallAmount = totalBonded * 0.10 / blocksPerYear  (positive)
   - topUpAmount = min(shortFallAmount, poolBalance)
   - RewardsPoolName decremented by topUpAmount
4. After N = ceil(F / shortFallAmount) blocks, RewardsPoolName.balance = 0.
5. User calls MsgExitTierWithDelegation:
   - claimRewards called (line 532)
   - processEventsAndClaimBonus: totalBonus > 0 (time has elapsed, BonusApy > 0)
   - sufficientBonusPoolBalance: poolBalance = 0 < bonus → ErrInsufficientBonusPool
   - ExitTierWithDelegation returns error; transferDelegationFromPosition never called
6. Assert: user's delegation is still locked in position delegator account.
   Assert: TierUndelegate, TierRedelegate, ClearPosition all fail identically.
``` [10](#0-9) [11](#0-10)

### Citations

**File:** x/tieredrewards/keeper/abci.go (L63-77)
```go
	targetStakersRewardPerBlock := math.LegacyNewDecFromInt(totalBonded).
		Mul(targetBaseRewardsRate).
		Quo(math.LegacyNewDec(int64(blocksPerYear)))

	feeCollector := k.accountKeeper.GetModuleAccount(ctx, authtypes.FeeCollectorName)
	if feeCollector == nil {
		k.logger(ctx).Error("fee collector module account not found, skipping base rewards top up")
		return nil
	}
	feeCollectorAddr := feeCollector.GetAddress()
	feeCollectorBalance := k.bankKeeper.GetBalance(ctx, feeCollectorAddr, bondDenom)
	defaultStakersRewardPerBlock := math.LegacyNewDecFromInt(feeCollectorBalance.Amount).
		MulTruncate(math.LegacyOneDec().Sub(communityTax))

	shortFallAmount := targetStakersRewardPerBlock.Sub(defaultStakersRewardPerBlock).TruncateInt()
```

**File:** x/tieredrewards/keeper/abci.go (L99-104)
```go
	if poolBalance.Amount.IsZero() {
		k.logger(ctx).Error("base rewards pool is empty, cannot top up validator rewards",
			"shortfall", shortFallAmount.String(),
		)
		return nil
	}
```

**File:** x/tieredrewards/keeper/abci.go (L105-113)
```go
	if poolBalance.Amount.LT(shortFallAmount) {
		k.logger(ctx).Error("base rewards pool has insufficient funds, distributing remaining balance",
			"shortfall", shortFallAmount.String(),
			"pool_balance", poolBalance.Amount.String(),
		)
		topUpAmount = poolBalance.Amount
	}

	err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, sdk.NewCoins(sdk.NewCoin(bondDenom, topUpAmount)))
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

**File:** x/tieredrewards/keeper/msg_server.go (L548-551)
```go
	transferredShares, unbondedShares, transferredAmount, err := ms.transferDelegationFromPosition(ctx, pos, valAddr, msg.Amount)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L48-59)
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L97-100)
```go
	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
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

**File:** x/tieredrewards/types/errors.go (L17-17)
```go
	ErrInsufficientBonusPool            = errors.Register(ModuleName, 12, "insufficient bonus pool balance")
```
