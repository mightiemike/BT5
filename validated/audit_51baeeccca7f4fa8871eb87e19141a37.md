### Title
Bonus Pool Depletion Blocks All Position Exit Operations, Locking Staked Tokens — (File: x/tieredrewards/keeper/claim_rewards.go)

---

### Summary

In the `x/tieredrewards` module, `claimRewards` is called as a **mandatory, non-skippable prerequisite** inside every critical position-exit message handler. If the bonus rewards pool (`RewardsPoolName`) has insufficient balance to cover a position's accrued bonus, `processEventsAndClaimBonus` returns an error that propagates upward and aborts the entire operation. This means a user with accrued bonus rewards cannot undelegate, redelegate, or exit their position when the pool is depleted — their staked tokens remain locked in the position's delegator sub-account with no recovery path.

---

### Finding Description

`processEventsAndClaimBonus` in `x/tieredrewards/keeper/claim_rewards.go` computes accrued bonus and, when non-zero, calls:

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err
}
// ...
if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
    return nil, err
}
``` [1](#0-0) 

If either call fails (pool balance insufficient), the error is returned to `claimRewards`:

```go
bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
if err != nil {
    return types.PositionState{}, nil, nil, err
}
``` [2](#0-1) 

`claimRewards` is then called unconditionally inside **every** critical exit handler:

| Handler | Call site |
|---|---|
| `TierUndelegate` | line 166 |
| `TierRedelegate` | line 229 |
| `AddToTierPosition` | line 314 |
| `ClearPosition` | line 406 |
| `ExitTierWithDelegation` | line 532 | [3](#0-2) [4](#0-3) [5](#0-4) 

None of these handlers have a fallback path that skips the bonus claim. The only exit path that does **not** call `claimRewards` is `WithdrawFromTier`, but it requires the position to already be in an undelegated state — which itself requires a successful `TierUndelegate` call first. [6](#0-5) 

---

### Impact Explanation

When the `RewardsPoolName` module account is depleted and a user has non-zero accrued bonus rewards, every exit path is blocked:

- `TierUndelegate` → fails → unbonding never starts → staked tokens remain locked in `pos.DelegatorAddress`
- `ExitTierWithDelegation` → fails → delegation never transferred back to owner
- `TierRedelegate` → fails → position cannot be moved to a different validator
- `ClearPosition` → fails → exit cannot be cancelled either

The staked tokens are held in a per-position delegator sub-account (`pos.DelegatorAddress`) created by `createPositionDelegatorAccount`. The owner has no direct access to this account; all access is mediated through the module's message handlers. With all handlers blocked, the tokens are effectively locked until the pool is externally refunded. [7](#0-6) 

---

### Likelihood Explanation

The bonus pool is a finite module account funded by governance or inflation. It can be depleted through:

1. **Normal operation**: many users claiming rewards simultaneously drains the pool faster than it is replenished.
2. **Governance inaction**: if no governance proposal to refill the pool passes in time, the pool stays empty.
3. **Adversarial drain**: an attacker with many positions can call `ClaimTierRewards` repeatedly to drain the pool, then all other users with accrued rewards are blocked from exiting.

`ClaimTierRewards` is a permissionless, unprivileged transaction callable by any position owner, making the drain path reachable by any participant. [8](#0-7) 

---

### Recommendation

Decouple the bonus reward claim from the exit/undelegate operations. Specifically:

1. In `TierUndelegate`, `ExitTierWithDelegation`, `TierRedelegate`, and `ClearPosition`, make the bonus claim **best-effort**: if `processEventsAndClaimBonus` fails due to insufficient pool balance, record the owed amount in state (a "pending bonus" field on the position) and allow the exit to proceed.
2. Provide a separate `ClaimPendingBonus` message that users can call once the pool is refunded.
3. Alternatively, ensure the pool can never be fully depleted by capping per-block payouts or enforcing a minimum reserve.

---

### Proof of Concept

1. User A creates a tier position via `MsgLockTier`, accruing bonus rewards over time.
2. Attacker (or organic usage) calls `MsgClaimTierRewards` across many positions, draining the `RewardsPoolName` module account to zero.
3. User A's position now has non-zero `totalBonus` computed in `processEventsAndClaimBonus` (line 161–213).
4. User A calls `MsgTierUndelegate` to begin withdrawing their staked tokens.
5. `claimRewards` is called at line 166; `processEventsAndClaimBonus` reaches `sufficientBonusPoolBalance` at line 230, which returns an error because the pool is empty.
6. `TierUndelegate` returns the error; the unbonding is never initiated.
7. User A's staked tokens remain locked in `pos.DelegatorAddress` indefinitely.
8. `ExitTierWithDelegation` and `TierRedelegate` fail identically.
9. `WithdrawFromTier` cannot be reached because it requires the position to be undelegated first. [9](#0-8) [10](#0-9)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L97-100)
```go
	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
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

**File:** x/tieredrewards/keeper/msg_server.go (L52-58)
```go
	delAddr, err := ms.createPositionDelegatorAccount(ctx, ownerAddr, id)
	if err != nil {
		return nil, err
	}

	if err := ms.lockFunds(ctx, ownerAddr, delAddr, msg.Amount); err != nil {
		return nil, err
```

**File:** x/tieredrewards/keeper/msg_server.go (L152-208)
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
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L229-232)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L429-468)
```go
func (ms msgServer) ClaimTierRewards(ctx context.Context, msg *types.MsgClaimTierRewards) (*types.MsgClaimTierRewardsResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	positions := make([]types.PositionState, 0, len(msg.PositionIds))
	for _, posId := range msg.PositionIds {
		pos, err := ms.getPositionState(ctx, posId)
		if err != nil {
			return nil, err
		}

		if err := ms.validateClaimRewards(pos.Position, msg.Owner); err != nil {
			return nil, err
		}

		positions = append(positions, pos)
	}

	totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventTierRewardsClaimed{
		Owner:        msg.Owner,
		PositionIds:  msg.PositionIds,
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
	}); err != nil {
		return nil, err
	}

	return &types.MsgClaimTierRewardsResponse{
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
		PositionIds:  msg.PositionIds,
	}, nil
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L470-516)
```go
func (ms msgServer) WithdrawFromTier(ctx context.Context, msg *types.MsgWithdrawFromTier) (*types.MsgWithdrawFromTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateWithdrawFromTier(ctx, pos, msg.Owner); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	balances := ms.bankKeeper.SpendableCoins(ctx, delAddr)
	if !balances.IsZero() {
		if err := ms.bankKeeper.SendCoins(ctx, delAddr, ownerAddr, balances); err != nil {
			return nil, err
		}
	}

	if err := ms.deletePosition(ctx, pos.Position, nil); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionWithdrawn{
		Position: pos.Position,
		Amount:   balances,
	}); err != nil {
		return nil, err
	}

	return &types.MsgWithdrawFromTierResponse{
		Amount: balances,
	}, nil
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L532-535)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```
