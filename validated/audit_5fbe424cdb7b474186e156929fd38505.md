Based on my analysis of the tiered rewards module source code, I can identify a valid analog vulnerability.

---

### Title
Bonus Pool Depletion Blocks Principal Undelegation, Making User Funds Inaccessible — (`x/tieredrewards/keeper/claim_rewards.go`, `x/tieredrewards/keeper/msg_server.go`)

### Summary
When the `BonusRewardsPool` module account is depleted and a position has accrued non-zero bonus rewards, every operation that requires `claimRewards` as a prerequisite — including `TierUndelegate` and `ExitTierWithDelegation` — fails atomically. Users cannot recover their staked principal until governance refills the pool.

### Finding Description
`claimRewards` is called unconditionally before any state-mutating exit operation:

- `TierUndelegate` — [1](#0-0) 
- `TierRedelegate` — [2](#0-1) 
- `ExitTierWithDelegation` — [3](#0-2) 
- `ClearPosition` — [4](#0-3) 

Inside `claimRewards`, `processEventsAndClaimBonus` is called: [5](#0-4) 

`processEventsAndClaimBonus` computes `totalBonus` and, when it is non-zero, calls `sufficientBonusPoolBalance`: [6](#0-5) 

`sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` if the module account balance is below the owed bonus: [7](#0-6) 

Because this error propagates back through `claimRewards` and the calling message handler returns it, the entire transaction is rolled back. The user's delegation remains locked in the position's derived delegator address with no alternative exit path.

The only operations that do **not** call `claimRewards` are `TriggerExitFromTier` (sets the exit timer) and `WithdrawFromTier` (sweeps liquid balance after undelegation completes). `WithdrawFromTier` is unreachable until `TierUndelegate` or `ExitTierWithDelegation` succeeds — both of which are blocked. [8](#0-7) 

### Impact Explanation
A user who has accrued any non-zero bonus rewards cannot undelegate or transfer their staked principal out of the tiered rewards module while the `BonusRewardsPool` is empty. Their tokens are locked in the position's derived delegator address with no permissionless escape hatch. This is a direct analog to the external report: the user's full principal is committed to the module, but the module's pool state (analogous to template caps) prevents any portion from being reclaimed until an external actor (governance) corrects the pool balance.

### Likelihood Explanation
The `BonusRewardsPool` is a finite module account funded by governance or inflation. Under high participation or a sustained high `BonusApy`, the pool can be drained by normal `ClaimTierRewards` calls from any set of users. No privileged access is required to trigger the depletion — any combination of legitimate claimants can exhaust the pool. Once empty, every position with accrued bonus is simultaneously blocked from undelegating.

### Recommendation
Decouple the bonus pool sufficiency check from the undelegation and exit code paths. `claimRewards` should be split so that base reward withdrawal and bonus accrual checkpointing proceed independently of the pool balance check. Specifically, `TierUndelegate` and `ExitTierWithDelegation` should be able to advance the `LastBonusAccrual` checkpoint and record the owed bonus as a claimable debt without requiring the pool to be solvent at that moment. The bonus can then be paid lazily when the pool is refilled, while the principal is immediately unblocked.

### Proof of Concept
1. User calls `MsgLockTier` — principal is delegated via the position's derived address. [9](#0-8) 
2. Time passes; `computeSegmentBonus` accumulates a non-zero `totalBonus` for the position. [10](#0-9) 
3. Other users drain the `BonusRewardsPool` via `MsgClaimTierRewards`.
4. User calls `MsgTierUndelegate`. The handler calls `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` → returns `ErrInsufficientBonusPool`. Transaction reverts. [11](#0-10) 
5. User calls `MsgExitTierWithDelegation`. Same failure path. [3](#0-2) 
6. `MsgWithdrawFromTier` is unreachable because it requires a completed undelegation. Principal remains locked indefinitely until governance refills the pool.

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L57-63)
```go
	if err := ms.lockFunds(ctx, ownerAddr, delAddr, msg.Amount); err != nil {
		return nil, err
	}

	if _, err := ms.delegate(ctx, delAddr, valAddr, msg.Amount); err != nil {
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

**File:** x/tieredrewards/keeper/msg_server.go (L406-409)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
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
