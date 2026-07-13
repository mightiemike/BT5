### Title
Insufficient Bonus Pool Balance Blocks Valid Position Exits and Undelegations — (`x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

In the `tieredrewards` module, all position-exit and undelegation operations unconditionally call `claimRewards`, which internally calls `processEventsAndClaimBonus`. If the bonus rewards pool has insufficient balance, `processEventsAndClaimBonus` returns a hard error that propagates up and aborts the entire operation — including `TierUndelegate`, `TierRedelegate`, `ExitTierWithDelegation`, and `ClearPosition`. The underlying delegation operations are valid regardless of bonus pool balance, yet users are blocked from exiting their positions. The slash handler in the same module already demonstrates the correct fix: forgo bonus rewards when the pool is insufficient rather than aborting.

---

### Finding Description

`processEventsAndClaimBonus` computes accrued bonus rewards and, before transferring them, checks whether the pool can cover the payout: [1](#0-0) 

If `totalBonus` is non-zero but the pool is short, the function returns `nil, err`. This error propagates through `claimRewards`: [2](#0-1) 

Every message handler that must claim rewards before mutating position state calls `claimRewards` and propagates the error unconditionally:

- `TierUndelegate` — [3](#0-2) 
- `TierRedelegate` — [4](#0-3) 
- `ExitTierWithDelegation` — [5](#0-4) 
- `ClearPosition` — [6](#0-5) 
- `AddToTierPosition` — [7](#0-6) 

The slash handler in the same module already demonstrates the correct behavior: when `ErrInsufficientBonusPool` is encountered, bonus rewards are forfeited and the operation proceeds to prevent a chain halt: [8](#0-7) 

The user-facing exit paths apply no such grace handling, creating an asymmetry: slashing can always proceed, but a user's voluntary exit cannot.

---

### Impact Explanation

When a user has accrued non-zero bonus rewards but the `RewardsPoolName` module account holds less than the owed amount, every attempt to undelegate, redelegate, exit with delegation, or clear a position fails with `ErrInsufficientBonusPool`. The user's delegation remains locked inside the position account indefinitely. Because the position's delegator address is a derived account controlled solely by the module, the user has no alternative path to recover their staked tokens. This constitutes a loss of access to user funds for an indeterminate period.

---

### Likelihood Explanation

The bonus pool is a finite module account funded by inflation or governance. It can be drained to zero by a burst of reward claims (e.g., end-of-epoch processing via `claimRewardsAndUpdateTierPositions` in `abci.go`, or many users calling `ClaimTierRewards` simultaneously). Once drained, any user who has accrued even one unit of bonus reward is blocked from exiting. This is a realistic operational condition, not a theoretical edge case.

---

### Recommendation

In `claimRewards` (or in each affected message handler), catch `ErrInsufficientBonusPool` and proceed with the operation while forfeiting the unpayable bonus rewards — exactly as `slashRedelegationPosition` does. Bonus rewards that cannot be paid should be logged and silently dropped rather than aborting the exit.

```go
bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
if err != nil {
    if !errors.Is(err, types.ErrInsufficientBonusPool) {
        return types.PositionState{}, nil, nil, err
    }
    // forgo bonus; allow exit to proceed
    bonus = sdk.NewCoins()
}
```

---

### Proof of Concept

1. Bonus pool is drained to zero (e.g., by end-of-block `claimRewardsAndUpdateTierPositions` or mass `ClaimTierRewards` calls).
2. User's position has accrued non-zero bonus rewards (`totalBonus > 0` in `processEventsAndClaimBonus`).
3. User submits `MsgTierUndelegate` to exit their position.
4. `TierUndelegate` calls `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`.
5. `TierUndelegate` returns the error; the undelegation is never executed.
6. User's delegation remains locked in the position account with no recovery path until the pool is externally replenished.
7. The same failure occurs for `TierRedelegate`, `ExitTierWithDelegation`, `ClearPosition`, and `AddToTierPosition`.

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

**File:** x/tieredrewards/keeper/msg_server.go (L314-317)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L406-409)
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
