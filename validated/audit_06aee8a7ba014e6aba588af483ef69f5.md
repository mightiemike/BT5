### Title
Mandatory Bonus Claim Blocks Position Exit When Bonus Pool Is Depleted — (`x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

Every exit and undelegation path in the tiered-rewards module unconditionally calls `claimRewards`, which in turn calls `processEventsAndClaimBonus`. If the user has accrued non-zero bonus rewards and the bonus pool is depleted, `sufficientBonusPoolBalance` returns an error that propagates all the way up, causing the exit/undelegate transaction to revert. The user's staked tokens remain locked in the position with no bypass path until the pool is replenished.

---

### Finding Description

`processEventsAndClaimBonus` computes the total bonus owed to a position and then gates the payout on a pool-balance check: [1](#0-0) 

If `totalBonus` is non-zero and the pool cannot cover it, the function returns an error. That error is not handled gracefully — it is returned directly from `claimRewards`: [2](#0-1) 

`claimRewards` is called as a **mandatory, non-skippable step** inside every message handler that modifies or exits a position:

| Handler | Call site |
|---|---|
| `TierUndelegate` | line 166 |
| `TierRedelegate` | line 229 |
| `AddToTierPosition` | line 314 |
| `ClearPosition` | line 406 |
| `ExitTierWithDelegation` | line 532 | [3](#0-2) [4](#0-3) 

None of these handlers catch or suppress the error from `claimRewards`; they all propagate it directly to the caller, causing the entire transaction to revert.

---

### Impact Explanation

A user who has accumulated non-zero bonus rewards cannot undelegate, redelegate, add to, clear, or exit their position while the bonus pool is depleted. Their staked tokens remain locked inside the position-delegator account with no alternative exit path. The lockup is indefinite — it persists until governance or the protocol refills the pool. This is a fund-lockup, not merely a service degradation.

**Impact: Medium**

---

### Likelihood Explanation

The bonus pool is a finite module account funded by the protocol. As positions accumulate and claim rewards over time, the pool balance decreases. Any user who has held a delegated position long enough to accrue a non-zero bonus is affected the moment the pool balance falls below their owed amount. No privileged action is required; normal reward-claiming activity across all positions is sufficient to deplete the pool. The condition is reachable through ordinary `MsgClaimTierRewards` transactions submitted by any unprivileged delegator.

**Likelihood: Medium**

---

### Recommendation

Decouple the bonus payout from the exit/undelegate flow. Concretely:

1. In `processEventsAndClaimBonus`, when `sufficientBonusPoolBalance` fails, cap the payout at the available pool balance (pay what is available, record the remainder as a claimable debt) rather than reverting.
2. Alternatively, allow `TierUndelegate` and `ExitTierWithDelegation` to proceed even when the bonus claim cannot be fully satisfied, deferring the unpaid bonus to a separate claimable record that the user can collect once the pool is refilled.

Either approach breaks the hard coupling between "bonus pool has sufficient funds" and "user can exit their position."

---

### Proof of Concept

1. Alice creates a tiered-rewards position via `MsgLockTier` and delegates to a bonded validator.
2. Over several blocks, Alice's position accrues non-zero bonus rewards (validator events are recorded, `LastEventSeq` advances).
3. The bonus pool balance falls below Alice's owed bonus amount — either through natural reward distribution to other positions or through an attacker submitting many `MsgClaimTierRewards` transactions to drain the pool.
4. Alice submits `MsgTierUndelegate` to exit her position.
5. Execution reaches `ms.claimRewards(ctx, pos)` at line 166 of `msg_server.go`.
6. Inside `claimRewards`, `processEventsAndClaimBonus` computes `totalBonus > 0` and calls `sufficientBonusPoolBalance`, which returns an error because the pool is depleted.
7. The error propagates: `processEventsAndClaimBonus` → `claimRewards` → `TierUndelegate` → transaction revert.
8. Alice's staked tokens remain locked in the position-delegator account. She cannot undelegate, redelegate, or exit via any message handler until the pool is refilled. [5](#0-4) [6](#0-5)

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

**File:** x/tieredrewards/keeper/msg_server.go (L163-170)
```go
		return nil, err
	}

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
