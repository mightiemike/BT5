### Title
Bonus Pool Exhaustion Blocks Base Reward Claims in `claimRewards` — (File: `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

When the tiered-rewards bonus pool (`RewardsPoolName`) is exhausted, the `claimRewards` function fails entirely because `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`. This error propagates back through `claimRewards`, causing the entire transaction to revert — including the already-executed `claimBaseRewards` withdrawal. Users with accrued bonus rewards are therefore permanently blocked from claiming their independently-accrued base staking rewards until the pool is externally refilled.

---

### Finding Description

`claimRewards` sequences two independent reward operations:

1. `claimBaseRewards` — withdraws standard staking rewards from the distribution module.
2. `processEventsAndClaimBonus` — computes and pays bonus rewards from the `RewardsPoolName` module account. [1](#0-0) 

Inside `processEventsAndClaimBonus`, after computing a non-zero `totalBonus`, the code calls `sufficientBonusPoolBalance`: [2](#0-1) 

`sufficientBonusPoolBalance` returns a hard error when the pool balance is insufficient: [3](#0-2) 

This error propagates back to `claimRewards`: [4](#0-3) 

Because the Cosmos SDK message handler reverts all state changes on error, the `claimBaseRewards` withdrawal (which already executed at line 92) is also rolled back. The user receives nothing — not even their base staking rewards, which are entirely independent of the bonus pool.

The contrast with `topUpBaseRewards` in the BeginBlocker is instructive: that function explicitly handles pool exhaustion gracefully by logging and returning `nil`: [5](#0-4) 

No equivalent graceful degradation exists in the user-facing `claimRewards` path.

---

### Impact Explanation

Any user whose position has accrued non-zero bonus rewards cannot claim their base staking rewards while the bonus pool is exhausted. Base rewards continue to accrue in the distribution module but are inaccessible through the tiered-rewards claim path. The blocked value is the user's accumulated base staking rewards (standard CRO delegation rewards), not just the bonus. This is a denial of access to user funds, not merely reward dilution.

**Impact: Medium**

---

### Likelihood Explanation

The bonus pool is a finite module account funded by governance or protocol allocation. As positions accrue and claim bonus rewards over time, the pool balance decreases. Once exhausted — with no automatic refill mechanism — every user who has accrued non-zero bonus rewards is blocked from claiming base rewards. This is a reachable, unprivileged, normal-transaction trigger (any `MsgClaimRewards` from a position owner).

**Likelihood: Medium**

---

### Recommendation

Decouple the bonus pool failure from the base rewards claim. When `sufficientBonusPoolBalance` returns an error, `processEventsAndClaimBonus` should either:

1. **Cap the bonus at the available pool balance** and pay what is available (matching the pattern already used in `topUpBaseRewards`), or
2. **Return `(sdk.NewCoins(), nil)`** (zero bonus, no error) when the pool is insufficient, so that base rewards are still claimable and the bonus accrual checkpoint is not advanced (preserving the owed bonus for a future claim).

Option 2 preserves the invariant that owed bonus is never silently lost, while unblocking base reward claims.

---

### Proof of Concept

1. Deploy the chain with a tiered-rewards position that has accrued non-zero bonus rewards.
2. Drain the `RewardsPoolName` module account to zero (e.g., via repeated successful bonus claims by other positions, or by governance).
3. Submit `MsgClaimRewards` for the position from step 1.
4. Observe: the transaction fails with `ErrInsufficientBonusPool`.
5. The user's base staking rewards (which exist in the distribution module and are independent of the bonus pool) are not paid out. The transaction reverts entirely.
6. The user is blocked from claiming base rewards until the bonus pool is externally refilled.

The root cause is at `x/tieredrewards/keeper/claim_rewards.go` lines 97–100, where the error from `processEventsAndClaimBonus` causes `claimRewards` to return early with an error, discarding the already-executed base rewards withdrawal. [6](#0-5)

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

**File:** x/tieredrewards/keeper/abci.go (L99-104)
```go
	if poolBalance.Amount.IsZero() {
		k.logger(ctx).Error("base rewards pool is empty, cannot top up validator rewards",
			"shortfall", shortFallAmount.String(),
		)
		return nil
	}
```
