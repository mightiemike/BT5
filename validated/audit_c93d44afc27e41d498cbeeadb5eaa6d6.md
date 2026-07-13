### Title
Governance `UpdateTier` Permanently Blocked When Rewards Pool Is Insufficient - (`File: x/tieredrewards/keeper/msg_server_auth.go`)

### Summary

The governance-only `MsgUpdateTier` handler unconditionally calls `claimRewardsAndUpdateTierPositions` whenever `BonusApy` changes. That sweep calls `processEventsAndClaimBonus` for every delegated position in the tier, which hard-reverts with `ErrInsufficientBonusPool` if the rewards pool cannot cover all accrued bonus. Because the rewards pool is routinely drained by the `BeginBlocker` base-rewards top-up, governance loses the ability to update any tier's `BonusApy` whenever the pool is empty — the exact same recovery-blocking pattern as the reference report.

### Finding Description

In `UpdateTier`, when the new `BonusApy` differs from the stored value, the handler calls `claimRewardsAndUpdateTierPositions` before writing the new tier:

```go
// x/tieredrewards/keeper/msg_server_auth.go:67-71
if !oldTier.BonusApy.Equal(msg.Tier.BonusApy) {
    if err := ms.claimRewardsAndUpdateTierPositions(ctx, msg.Tier.Id); err != nil {
        return nil, err
    }
}
``` [1](#0-0) 

`claimRewardsAndUpdateTierPositions` iterates every delegated position in the tier and calls `processEventsAndClaimBonus` on each:

```go
// x/tieredrewards/keeper/claim_rewards.go:68-73
if _, err := k.claimBaseRewards(ctx, pos); err != nil {
    return err
}
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    return err
}
``` [2](#0-1) 

`processEventsAndClaimBonus` calls `sufficientBonusPoolBalance`, which returns a hard error — not a soft skip — when the pool balance is below the accrued bonus:

```go
// x/tieredrewards/keeper/claim_rewards.go:230-232
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err
}
``` [3](#0-2) 

```go
// x/tieredrewards/keeper/bonus_rewards.go:48-61
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
    ...
    if !poolBalance.IsAllGTE(bonus) {
        return errorsmod.Wrapf(types.ErrInsufficientBonusPool, ...)
    }
    return nil
}
``` [4](#0-3) 

The `BeginBlocker` drains the pool every block to top up base rewards, and explicitly handles an empty pool by logging and returning `nil` — it does **not** revert: [5](#0-4) 

The slash hook (`slashRedelegationPosition`) also explicitly catches `ErrInsufficientBonusPool` and continues rather than reverting: [6](#0-5) 

`claimRewardsAndUpdateTierPositions` has no such protection — it propagates the error directly to `UpdateTier`, which propagates it to the caller, reverting the entire governance transaction.

### Impact Explanation

Governance cannot change any tier's `BonusApy` while the rewards pool is insufficient to pay all accrued bonus for every delegated position in that tier. Because the pool is continuously drained by the `BeginBlocker`, this condition is the normal steady state unless the pool is actively over-funded. The corrupted invariant is: **governance authority over tier parameters is permanently suspended** for any tier with delegated positions and accrued bonus, until an external actor funds the pool to cover the full outstanding bonus of all positions in that tier simultaneously. This blocks legitimate protocol management (e.g., reducing an over-generous APY, correcting a misconfigured tier).

**Impact: Medium** — governance action is blocked; no direct user fund loss, but protocol management is disabled.

### Likelihood Explanation

The rewards pool is drained every block by `topUpBaseRewards` whenever there is a base-rewards shortfall. Any tier with delegated positions that have accrued bonus (i.e., any tier that has been live for more than a few seconds) will trigger this revert whenever the pool is empty. The pool being empty is the expected state between funding events.

**Likelihood: Medium** — the pool is routinely empty; any governance proposal to change a tier's `BonusApy` will fail in this state.

### Recommendation

In `claimRewardsAndUpdateTierPositions`, handle `ErrInsufficientBonusPool` the same way `slashRedelegationPosition` does — log the error and skip the bonus payout for that position rather than reverting. Alternatively, decouple the forced claim from `UpdateTier`: do not call `claimRewardsAndUpdateTierPositions` inside the governance handler. Instead, snapshot the old `BonusApy` on each position at claim time (lazy evaluation), so governance can always update tier parameters regardless of pool state.

### Proof of Concept

1. A tier (ID=1) has N delegated positions with accrued bonus.
2. The rewards pool is empty (drained by `BeginBlocker` top-up, normal state).
3. Governance submits `MsgUpdateTier` with a new `BonusApy` for tier 1.
4. `UpdateTier` detects `BonusApy` changed → calls `claimRewardsAndUpdateTierPositions(ctx, 1)`.
5. For the first delegated position, `processEventsAndClaimBonus` computes `totalBonus > 0`.
6. `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` (pool balance = 0 < bonus).
7. The error propagates: `claimRewardsAndUpdateTierPositions` → `UpdateTier` → transaction reverts.
8. The tier's `BonusApy` is never updated. Governance cannot retry until the pool is funded to cover the full accrued bonus of all positions in the tier simultaneously.

The entry path is a standard governance `MsgUpdateTier` transaction, reachable by any governance participant. No privileged key or social engineering is required beyond the normal governance quorum. [7](#0-6) [2](#0-1) [4](#0-3)

### Citations

**File:** x/tieredrewards/keeper/msg_server_auth.go (L57-82)
```go
func (ms msgServer) UpdateTier(ctx context.Context, msg *types.MsgUpdateTier) (*types.MsgUpdateTierResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	oldTier, err := ms.getTier(ctx, msg.Tier.Id)
	if err != nil {
		return nil, err
	}

	if !oldTier.BonusApy.Equal(msg.Tier.BonusApy) {
		if err := ms.claimRewardsAndUpdateTierPositions(ctx, msg.Tier.Id); err != nil {
			return nil, err
		}
	}

	if err := ms.SetTier(ctx, msg.Tier); err != nil {
		return nil, err
	}

	if err := ms.emitTierChangedEvent(ctx, types.TierChangeAction_TIER_CHANGE_ACTION_UPDATE, msg.Tier); err != nil {
		return nil, err
	}

	return &types.MsgUpdateTierResponse{}, nil
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

**File:** x/tieredrewards/keeper/abci.go (L99-111)
```go
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
