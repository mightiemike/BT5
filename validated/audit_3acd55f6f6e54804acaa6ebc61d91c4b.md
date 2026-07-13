### Title
Mandatory Bonus-Pool Solvency Check in `processEventsAndClaimBonus` Blocks All Exit Paths, Permanently Locking User Principal - (`File: x/tieredrewards/keeper/bonus_rewards.go`)

---

### Summary

Every critical exit and mutation operation in the tiered-rewards module calls `claimRewards` as a mandatory, atomic prerequisite. `claimRewards` internally calls `processEventsAndClaimBonus`, which hard-fails with `ErrInsufficientBonusPool` if the `RewardsPoolName` module account cannot cover the accrued bonus. Because `TierUndelegate` and `ExitTierWithDelegation` both gate on this call, a user whose position has accrued any non-zero bonus cannot undelegate or exit when the pool is empty — their staked principal is permanently locked until governance refills the pool.

---

### Finding Description

`processEventsAndClaimBonus` computes the total bonus owed to a position and then calls `sufficientBonusPoolBalance` before transferring funds:

```go
// x/tieredrewards/keeper/bonus_rewards.go:48-61
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
    ...
    if !poolBalance.IsAllGTE(bonus) {
        return errorsmod.Wrapf(types.ErrInsufficientBonusPool, ...)
    }
    return nil
}
``` [1](#0-0) 

This check is reached unconditionally for any position with `totalBonus > 0`:

```go
// x/tieredrewards/keeper/claim_rewards.go:230-232
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err
}
``` [2](#0-1) 

Every exit and mutation message handler calls `claimRewards` (which calls `processEventsAndClaimBonus`) as a mandatory step before performing its state change:

| Handler | Line | Effect if `claimRewards` fails |
|---|---|---|
| `TierUndelegate` | 166 | Cannot start unbonding |
| `ExitTierWithDelegation` | 532 | Cannot exit with delegation |
| `TierRedelegate` | 229 | Cannot move to another validator |
| `AddToTierPosition` | 314 | Cannot add funds |
| `ClearPosition` | 406 | Cannot cancel exit | [3](#0-2) [4](#0-3) 

`WithdrawFromTier` does not call `claimRewards`, but it requires `!pos.IsDelegated()`:

```go
// x/tieredrewards/keeper/msg_validate.go:197-199
if pos.IsDelegated() {
    return types.ErrPositionDelegated
}
``` [5](#0-4) 

To reach `WithdrawFromTier`, the user must first call `TierUndelegate` — which itself calls `claimRewards`. There is no exit path that bypasses the bonus-pool check for a delegated position with accrued bonus.

The same `RewardsPoolName` module account is also drained every block by the `BeginBlocker`'s `topUpBaseRewards`, which transfers from the pool to the distribution module whenever fee-collector income falls short of `TargetBaseRewardsRate`:

```go
// x/tieredrewards/keeper/abci.go:113
err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, ...)
``` [6](#0-5) 

The BeginBlocker handles an empty pool gracefully (logs and returns `nil`), but `processEventsAndClaimBonus` does not — it returns a hard error. This asymmetry means the pool can be silently drained by the BeginBlocker while user exit paths remain hard-blocked.

The `ForceFullExitWithDelegation` migration helper also calls `claimRewards` and would fail identically:

```go
// x/tieredrewards/keeper/force_exit.go:37-40
posState, baseRewards, bonusRewards, err := k.claimRewards(ctx, posState)
if err != nil {
    return fmt.Errorf("claim rewards for position %d: %w", posID, err)
}
``` [7](#0-6) 

---

### Impact Explanation

**Impact: High.** A user who has locked tokens into a tier and accrued any non-zero bonus cannot recover their staked principal via any on-chain message when the pool is empty. `TierUndelegate` and `ExitTierWithDelegation` both fail atomically. The principal remains locked in the module's delegator sub-account indefinitely. This is not a reward-only freeze — it is a principal freeze.

---

### Likelihood Explanation

**Likelihood: Low.** The pool must be empty or have a balance below the user's accrued bonus at the time of the exit attempt. This can occur naturally as the pool is consumed by both bonus reward claims and the per-block `topUpBaseRewards` BeginBlocker drain. If governance does not refill the pool in time, any user with accrued bonus is blocked. The scenario requires no attacker — it is a normal operational condition.

---

### Recommendation

Decouple the bonus-pool solvency check from the mandatory reward-settlement step that gates exit paths. Specifically:

1. In `processEventsAndClaimBonus`, if the pool balance is insufficient, **advance the position's checkpoints** (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) and **record the owed-but-unpaid bonus as a debt** on the position rather than returning an error.
2. Allow `TierUndelegate` and `ExitTierWithDelegation` to proceed even when bonus cannot be paid immediately, paying out the debt when the pool is refilled or at a later claim.
3. Alternatively, separate the reward-settlement call from the exit-path precondition: exit paths should only require that reward checkpoints are advanced (a pure state update), not that the transfer succeeds.

---

### Proof of Concept

1. User calls `MsgLockTier` and locks tokens into a tier with `BonusApy > 0`. Position is created with `LastBonusAccrual = block_time`.
2. Time passes. Bonus accrues. The `RewardsPoolName` account is drained to zero by the BeginBlocker's `topUpBaseRewards` over many blocks, or by other users claiming bonus rewards.
3. User triggers exit: `MsgTriggerExitFromTier`. This succeeds (no reward claim here).
4. Exit commitment elapses.
5. User calls `MsgTierUndelegate`. Handler calls `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance`. Pool balance is 0, accrued bonus is > 0. Returns `ErrInsufficientBonusPool`. Transaction reverts.
6. User calls `MsgExitTierWithDelegation`. Same call chain. Same failure.
7. User calls `MsgWithdrawFromTier`. Fails with `ErrPositionDelegated` because the position is still delegated (step 5 and 6 both failed).
8. User's principal is locked. No on-chain path exists to recover it until governance refills the pool. [8](#0-7) [9](#0-8)

### Citations

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-232)
```go
func (k Keeper) processEventsAndClaimBonus(ctx context.Context, pos *types.PositionState) (sdk.Coins, error) {
	// Rewards should have been claimed before undelegation
	if !pos.IsDelegated() {
		return sdk.NewCoins(), nil
	}

	valAddr, err := sdk.ValAddressFromBech32(pos.Delegation.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	blockTime := sdkCtx.BlockTime()

	totalBonus := math.ZeroInt()
	// Use the persisted bonded state from the last replay, not a hardcoded default.
	// This prevents overpaying bonus for unbonded gaps between claims.
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual

	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	for _, entry := range events {
		evt := entry.Event

		if bonded {
			// Compute bonus for the bonded segment [segmentStart, eventTime]
			// using the snapshot rate at the event.
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
		}

		// Update bonded state based on event type.
		switch evt.EventType {
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND:
			bonded = false
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND:
			bonded = true
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_SLASH:
			// Slash doesn't change bonded state.
		}

		segmentStart = evt.Timestamp
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
	}

	val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return nil, err
	}
	// Defensive: validator bond status check
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}

	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)

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

**File:** x/tieredrewards/keeper/msg_server.go (L518-535)
```go
func (ms msgServer) ExitTierWithDelegation(ctx context.Context, msg *types.MsgExitTierWithDelegation) (*types.MsgExitTierWithDelegationResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateExitTierWithDelegation(ctx, pos, msg.Owner, msg.Amount); err != nil {
		return nil, err
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L183-210)
```go
func (k Keeper) validateWithdrawFromTier(ctx context.Context, pos types.PositionState, owner string) error {
	if !pos.IsOwner(owner) {
		return types.ErrNotPositionOwner
	}

	if !pos.HasTriggeredExit() {
		return types.ErrExitNotTriggered
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if !pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationNotReached
	}

	if pos.IsDelegated() {
		return types.ErrPositionDelegated
	}

	isUnbonding, err := k.isUnbonding(ctx, pos.DelegatorAddress)
	if err != nil {
		return err
	}
	if isUnbonding {
		return types.ErrPositionUnbonding
	}

	return nil
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

**File:** x/tieredrewards/keeper/force_exit.go (L37-40)
```go
	posState, baseRewards, bonusRewards, err := k.claimRewards(ctx, posState)
	if err != nil {
		return fmt.Errorf("claim rewards for position %d: %w", posID, err)
	}
```
