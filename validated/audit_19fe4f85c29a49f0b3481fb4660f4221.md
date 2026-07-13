### Title
Bonus Rewards Permanently Lost When Pool Is Insufficient During Redelegation Slash — (File: `x/tieredrewards/keeper/slash.go`)

### Summary
In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the error is deliberately swallowed to avoid a chain halt. However, `processEventsAndClaimBonus` advances the position's `LastBonusAccrual` checkpoint and `LastEventSeq` **before** performing the pool balance check. The position is then saved with these advanced checkpoints, permanently skipping the accrued-but-unpaid bonus period. The user can never reclaim it.

### Finding Description

In `processEventsAndClaimBonus` (`x/tieredrewards/keeper/claim_rewards.go`), the function walks pending validator events, computes `totalBonus`, and then — critically — calls `applyBonusAccrualCheckpoint` and `UpdateLastKnownBonded` **before** checking whether the pool can cover the bonus: [1](#0-0) 

`applyBonusAccrualCheckpoint` advances `pos.LastBonusAccrual` to `blockTime`, and `UpdateLastEventSeq` (called inside the event loop) advances `pos.LastEventSeq` past all processed events. The pool balance check (`sufficientBonusPoolBalance`) and the actual coin transfer come only after these mutations. If the pool is insufficient, the function returns `ErrInsufficientBonusPool` with `pos` already carrying the advanced checkpoints.

In `slashRedelegationPosition`, this error is explicitly swallowed: [2](#0-1) 

For a partial slash, the code then saves the position with the advanced checkpoints: [3](#0-2) 

On the next `MsgClaimTierRewards` call, `processEventsAndClaimBonus` starts its segment computation from the already-advanced `pos.LastBonusAccrual`, permanently skipping the period `[old_LastBonusAccrual, slashBlockTime]`. There is no recovery path.

The design intent is documented in the ADR: [4](#0-3) 

However, the consequence is that earned bonus rewards are permanently destroyed rather than deferred.

### Impact Explanation

A tier position holder whose validator's redelegation is slashed while the bonus pool is insufficient permanently loses all bonus rewards accrued since their last claim. The corrupted value is `pos.LastBonusAccrual` — once advanced past the unpaid period and persisted, the accrued bonus for that segment is unrecoverable. This is a direct, permanent loss of user funds (bonus rewards), not a dilution or deferral.

### Likelihood Explanation

Three conditions must coincide:
1. A user has an active tier position currently in a staking redelegation (i.e., `MsgTierRedelegate` was called and the 21-day redelegation window has not yet closed).
2. The destination validator is slashed (downtime or double-sign) during that window.
3. The bonus pool (`tieredrewards_rewards_pool`) is empty or insufficient at that block.

The pool is actively drained every block by `topUpBaseRewards` in the `BeginBlocker`: [5](#0-4) 

With `TargetBaseRewardsRate > 0` (the production default is `0.03`), the pool drains continuously. A validator slash during a redelegation window while the pool is low is a realistic production scenario, particularly on a chain with active staking churn.

### Recommendation

Move `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` to **after** the pool balance check in `processEventsAndClaimBonus`, so that if the pool check fails, the position's checkpoints remain at their pre-call values. This allows the user to retry once the pool is replenished, consistent with the behavior documented for all other user-driven paths.

Alternatively, in `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, explicitly restore the original `LastBonusAccrual` and `LastEventSeq` on `pos` before calling `setPositionWithState`, so the position is saved without the advanced checkpoints.

### Proof of Concept

1. User calls `MsgLockTier` to create a position on validator A, then calls `MsgTierRedelegate` to move to validator B. A `RedelegationMapping` entry is created.
2. The `BeginBlocker` drains the bonus pool to zero over subsequent blocks (or the pool was never funded).
3. Validator B goes offline; the Cosmos SDK slashing module fires `BeforeRedelegationSlashed` with the position's `unbondingId`.
4. `slashRedelegationPosition` calls `processEventsAndClaimBonus(&pos)`:
   - The function walks events, computes `totalBonus > 0`, advances `pos.LastBonusAccrual` to `blockTime` and `pos.LastEventSeq` past all events.
   - The pool check fails → returns `ErrInsufficientBonusPool`.
5. The error is swallowed. `pos` now has `LastBonusAccrual = slashBlockTime` but no bonus was paid.
6. `setPositionWithState(ctx, pos, nil)` persists the position with the advanced checkpoint.
7. The pool is later replenished (e.g., governance funds it).
8. User calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts from `LastBonusAccrual = slashBlockTime`, skipping the entire pre-slash accrual period. The bonus for that period is permanently lost. [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-228)
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
```

**File:** x/tieredrewards/keeper/slash.go (L13-77)
```go
// slashRedelegationPosition settles bonus rewards for a tier position whose
// staking redelegation entry is about to be slashed, then reconciles
// post-slash checkpoints and the validator-position counter.
//
// Base rewards will already have been auto-withdrawn to the owner via
// distribution's BeforeDelegationSharesModified hook by the time this fires.
func (k Keeper) slashRedelegationPosition(ctx context.Context, unbondingId uint64, sharesToUnbond math.LegacyDec) error {
	positionId, err := k.getRedelegationMapping(ctx, unbondingId)
	if errors.Is(err, collections.ErrNotFound) {
		return nil
	}
	if err != nil {
		return err
	}

	pos, err := k.getPositionState(ctx, positionId)
	if errors.Is(err, types.ErrPositionNotFound) {
		k.logger(ctx).Error("position not found during redelegation slash",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"error", err.Error(),
		)
		return nil
	}
	if err != nil {
		return err
	}

	if !pos.IsDelegated() {
		// Defensive
		k.logger(ctx).Error("delegation missing during BeforeRedelegationSlashed",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"shares_to_unbond", sharesToUnbond.String(),
		)
		return nil
	}

	dstValStr := pos.Delegation.ValidatorAddress

	// Settle bonus against PRE-slash shares.
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

	fullSlash := sharesToUnbond.GTE(pos.Delegation.Shares)

	if fullSlash {
		pos.Delegation = nil
		pos.ClearBonusCheckpoints()
		return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
	}
	// In-memory only: the persisted Position carries no share count, and the
	// live delegation will reflect the post-Unbond shares on the next read.
	// Update the local copy so any follow-up logic in this call sees consistent state.
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** doc/architecture/adr-006.md (L349-349)
```markdown
- Pool balance is never exceeded. User paths fail atomically; the `BeforeRedelegationSlashed` hook forfeits bonus silently if the pool is short, to avoid chain halt.
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
