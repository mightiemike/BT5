### Title
`topUpBaseRewards` drains the shared `RewardsPool` without reserving accrued-but-unclaimed bonus rewards, causing bonus reward claims to fail — (`File: x/tieredrewards/keeper/abci.go`)

---

### Summary

The `BeginBlocker` calls `topUpBaseRewards` every block, which transfers funds from the `RewardsPool` module account to the `distribution` module. The same `RewardsPool` account is also the sole source for bonus reward payments. Because bonus rewards are computed lazily (not pre-allocated), the pool balance at any moment silently includes amounts already owed to position holders but not yet claimed. `topUpBaseRewards` uses the raw total pool balance with no deduction for those owed amounts, so it can drain the pool entirely, causing every subsequent `MsgClaimTierRewards` to revert with `ErrInsufficientBonusPool` and permanently denying users their accrued bonus rewards.

---

### Finding Description

**Shared pool, no reservation accounting**

Both reward streams draw from the same module account `types.RewardsPoolName`:

- `topUpBaseRewards` (base reward stream) — transfers `min(shortfall, poolBalance)` from `RewardsPool` to `distribution` every block. [1](#0-0) 

- `processEventsAndClaimBonus` (bonus reward stream) — sends accrued bonus coins from `RewardsPool` directly to the position owner. [2](#0-1) 

**The root cause**

`topUpBaseRewards` reads the raw pool balance and caps the transfer at that amount:

```go
poolBalance := k.bankKeeper.GetBalance(ctx, poolAddr, bondDenom)
topUpAmount := shortFallAmount
if poolBalance.Amount.LT(shortFallAmount) {
    topUpAmount = poolBalance.Amount   // entire pool can be transferred
}
``` [3](#0-2) 

Bonus rewards are calculated lazily via `computeSegmentBonus` — they are never pre-allocated or earmarked inside the pool. [4](#0-3) 

Therefore `poolBalance` at any block includes:
1. Funds genuinely available for future use.
2. Funds already mathematically owed to every position holder whose `LastBonusAccrual` is in the past but who has not yet called `MsgClaimTierRewards`.

`topUpBaseRewards` treats both categories as freely available and can transfer all of them to `distribution`.

**Broken invariant**

The invariant `RewardsPool.balance ≥ Σ(accrued_but_unclaimed_bonus_for_all_positions)` is never enforced. After a large top-up transfer, the pool balance can fall below the total owed bonus, making the check in `sufficientBonusPoolBalance` fail for every claimant: [5](#0-4) 

---

### Impact Explanation

Any user holding a tiered-rewards position whose bonus has accrued since their last claim will receive `ErrInsufficientBonusPool` when they call `MsgClaimTierRewards`. Because the pool was drained by base-reward top-ups, the accrued bonus is permanently unclaimable unless governance manually refills the pool. Users who claim frequently (every block or epoch) drain the pool less and collect their bonus before it is consumed; users who claim infrequently lose their accrued bonus entirely. This is a direct analog to the original report: frequent claimants benefit at the expense of infrequent claimants, and the total bonus paid out can exceed the pool's intended budget.

---

### Likelihood Explanation

`topUpBaseRewards` runs unconditionally in every `BeginBlocker`. The top-up mechanism is explicitly designed to fire whenever the fee collector falls short of `TargetBaseRewardsRate`, which is the normal steady-state condition (the pool exists precisely because fees are insufficient). Over time, or during any period of low transaction volume, the pool will be progressively drained. No attacker action is required; normal chain operation is sufficient to trigger the condition. [6](#0-5) 

---

### Recommendation

Maintain a separate on-chain counter `TotalAccruedBonusOwed` that is incremented when bonus accrues and decremented when it is claimed. In `topUpBaseRewards`, cap the available pool balance as:

```go
availableForTopUp := poolBalance.Amount.Sub(totalAccruedBonusOwed)
if availableForTopUp.IsNegative() {
    availableForTopUp = math.ZeroInt()
}
topUpAmount = math.MinInt(shortFallAmount, availableForTopUp)
```

Alternatively, use two separate module accounts — one exclusively for base-reward top-ups and one exclusively for bonus rewards — so that the two streams cannot interfere with each other.

---

### Proof of Concept

1. Alice and Bob each create a tiered-rewards position at block 1.
2. The `RewardsPool` is funded with 1000 CRO.
3. Over 1000 blocks, `topUpBaseRewards` fires each block with a shortfall of 1 CRO, draining the pool to 0.
4. Alice claims every block and collects her bonus each time before the pool is drained — she receives her full entitlement.
5. Bob has not claimed since block 1. At block 1001, Bob calls `MsgClaimTierRewards`. `sufficientBonusPoolBalance` finds `poolBalance = 0 < bonusOwed`, returns `ErrInsufficientBonusPool`, and Bob's transaction reverts. Bob's accrued bonus is lost.

The entry path is: normal `BeginBlocker` execution (no privileged role required) → `topUpBaseRewards` → `SendCoinsFromModuleToModule(RewardsPool, distribution, topUpAmount)` where `topUpAmount = poolBalance.Amount`. [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/abci.go (L16-18)
```go
func (k Keeper) BeginBlocker(ctx context.Context) error {
	return k.topUpBaseRewards(ctx)
}
```

**File:** x/tieredrewards/keeper/abci.go (L96-116)
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
	if err != nil {
		return err
	}
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
