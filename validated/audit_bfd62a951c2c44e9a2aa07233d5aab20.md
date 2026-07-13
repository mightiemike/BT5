### Title
Shared `RewardsPoolName` Account Drained by Both Base-Reward Top-Up and Bonus-Reward Claims, Causing Claim Failures for Tiered Position Holders - (File: `x/tieredrewards/keeper/abci.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

The `x/tieredrewards` module uses a single `"rewards_pool"` module account (`types.RewardsPoolName`) as the funding source for two independent, competing obligations: (1) per-block base-reward top-ups for **all** network delegators via `topUpBaseRewards`, and (2) bonus-reward payouts for **tiered position holders** via `processEventsAndClaimBonus`. Because both consumers draw from the same balance without any accounting separation or reservation, one can silently exhaust the pool before the other can be satisfied.

---

### Finding Description

`types.RewardsPoolName` is defined as the single constant `"rewards_pool"`: [1](#0-0) 

**Consumer 1 — Base reward top-up (every block):**

Every block, `BeginBlocker` calls `topUpBaseRewards`, which reads the pool balance and transfers up to the full shortfall amount from `RewardsPoolName` into the `distribution` module for allocation to all validators and their delegators: [2](#0-1) 

**Consumer 2 — Bonus reward claim (user-triggered):**

When a tiered position holder calls `MsgClaimTierRewards`, `processEventsAndClaimBonus` computes the accrued bonus and calls `sufficientBonusPoolBalance`, which checks the live balance of the same `RewardsPoolName` account: [3](#0-2) 

If the check passes, the bonus is immediately sent from `RewardsPoolName` to the owner: [4](#0-3) 

**The broken invariant:** Neither consumer reserves its share of the pool. The `sufficientBonusPoolBalance` check is a point-in-time snapshot that does not account for the continuous per-block drain by `topUpBaseRewards`. Conversely, `topUpBaseRewards` does not account for bonus obligations already accrued but not yet claimed by tiered position holders.

---

### Impact Explanation

Two concrete failure modes:

1. **Tiered position holders cannot claim legitimately accrued bonuses.** If `topUpBaseRewards` has drained the pool (e.g., during a period of low transaction fees and high `TargetBaseRewardsRate`), a subsequent `MsgClaimTierRewards` will fail with `ErrInsufficientBonusPool` even though the bonus was correctly accrued. The user's funds are not lost, but the claim is blocked until the pool is replenished externally.

2. **Regular stakers receive less than the target base reward rate.** If tiered position holders claim large accumulated bonuses (e.g., after a long lock period), the pool may be depleted before the next block's `topUpBaseRewards` runs. The `BeginBlocker` silently logs an error and returns `nil` without topping up, meaning all delegators receive lower-than-target staking rewards for that block.

Both outcomes corrupt the reward accounting invariant: the pool balance is simultaneously over-promised to two distinct classes of beneficiaries.

---

### Likelihood Explanation

The scenario is reachable by any unprivileged user holding a tiered position. The trigger is a standard `MsgClaimTierRewards` transaction. The pool depletion condition is realistic: the pool is funded by governance deposits and is finite; the `BeginBlocker` drains it every block proportional to `TotalBonded × TargetBaseRewardsRate / BlocksPerYear`. A pool that appears sufficient for bonus claims at deposit time may be exhausted by the time a 1-year or 5-year lock expires.

---

### Recommendation

Maintain two separate accounting balances (or two separate module accounts) within the rewards pool: one reserved for base-reward top-ups and one for bonus rewards. Alternatively, during `MsgLockTier` / `MsgCommitDelegationToTier`, pre-compute and escrow the maximum possible bonus for the position's lifetime so that `topUpBaseRewards` can only draw from the unencumbered remainder. At minimum, `topUpBaseRewards` should cap its draw to `poolBalance - totalOutstandingBonusObligations` rather than the full pool balance.

---

### Proof of Concept

1. Governance funds `RewardsPoolName` with 1,000,000 CRO.
2. Alice locks 500,000 CRO into a 1-year tier with `BonusApy = 0.10`. Her maximum bonus obligation is ~50,000 CRO.
3. Over the next year, `topUpBaseRewards` runs every block. With `TotalBonded = 100M CRO`, `TargetBaseRewardsRate = 0.08`, and `BlocksPerYear = 5,256,000`, the per-block draw is ~1,522 CRO. Over 1 year the pool is fully drained by base-reward top-ups alone.
4. Alice calls `MsgClaimTierRewards` after her lock expires. `sufficientBonusPoolBalance` finds `poolBalance = 0`, and the transaction fails with `ErrInsufficientBonusPool`.
5. Alice's 50,000 CRO bonus is permanently unclaimable until an external governance deposit refills the pool — a deposit that will again be competed for by the next round of base-reward top-ups.

The root cause is the absence of any reservation or separation between the two consumers of `RewardsPoolName`: [5](#0-4) [6](#0-5)

### Citations

**File:** x/tieredrewards/types/keys.go (L23-23)
```go
	RewardsPoolName = "rewards_pool"
```

**File:** x/tieredrewards/keeper/abci.go (L96-113)
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

	err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, sdk.NewCoins(sdk.NewCoin(bondDenom, topUpAmount)))
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-241)
```go
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
