### Title
Bonus Accrual Checkpoint Advanced Without Payment on Partial Redelegation Slash with Insufficient Pool — (`File: x/tieredrewards/keeper/slash.go`)

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the error is deliberately swallowed to avoid a chain halt. However, `processEventsAndClaimBonus` advances the position's `LastBonusAccrual` checkpoint **in memory before** performing the pool balance check. After the error is swallowed, the position is persisted with the advanced checkpoint. The bonus that accrued up to the slash time is permanently forfeited — it can never be reclaimed because the checkpoint has moved past the accrual window.

### Finding Description

`processEventsAndClaimBonus` in `x/tieredrewards/keeper/claim_rewards.go` follows this order:

1. Walks pending validator events and accumulates `totalBonus`.
2. Calls `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` — advancing `LastBonusAccrual` to `blockTime` **in the caller's `pos` struct** (passed by pointer).
3. Calls `pos.UpdateLastKnownBonded(bonded)`.
4. If `totalBonus.IsZero()`, returns early (no issue).
5. Checks `sufficientBonusPoolBalance` — if the pool is short, returns `ErrInsufficientBonusPool`. [1](#0-0) 

Steps 2–3 mutate the `pos` object before step 5 can fail. When step 5 returns an error, the checkpoint has already been advanced.

In `slashRedelegationPosition` (`slash.go`), the `ErrInsufficientBonusPool` error is caught and execution continues: [2](#0-1) 

For a **partial slash** (`sharesToUnbond < pos.Delegation.Shares`), the code falls through to:

```go
pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
return k.setPositionWithState(ctx, pos, nil)
```

This persists `pos` with `LastBonusAccrual = blockTime` even though the bonus was never paid. On every subsequent claim, `processEventsAndClaimBonus` starts its segment from `blockTime`, permanently skipping the accrual window that was never compensated.

For a **full slash**, `pos.ClearBonusCheckpoints()` is called before `setPositionWithState`, which resets `LastBonusAccrual` to zero — but the position is also undelegated (`pos.Delegation = nil`), so no future bonus accrues regardless. The accrued-but-unpaid bonus is still lost. [3](#0-2) 

### Impact Explanation

A position owner whose redelegating position is **partially slashed** while the bonus pool is insufficient permanently loses all bonus rewards that accrued from the previous `LastBonusAccrual` up to the slash block time. The corrupted state variable is `pos.LastBonusAccrual` — it is set to `blockTime` as if payment succeeded, but no `SendCoinsFromModuleToAccount` was executed. The rewards pool balance is not reduced, but the owner's entitlement is silently erased.

The corrupted invariant: `LastBonusAccrual` must only advance when the corresponding bonus has been successfully transferred to the owner.

### Likelihood Explanation

The trigger requires three concurrent conditions:

1. A position has been redelegated via `MsgTierRedelegate` (a normal user action).
2. The destination validator is slashed during the redelegation period (a normal protocol event).
3. The bonus pool balance is below the accrued bonus at slash time.

Condition 3 is realistic: the pool is a shared resource drained by all position claims and by the `BeginBlocker` base-rewards top-up. A pool running low is an expected operational state. No privileged access or social engineering is required — the slash is triggered by the staking module's evidence handling, and the position owner is a normal unprivileged delegator.

### Recommendation

Move `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` to **after** the successful `SendCoinsFromModuleToAccount` call, so the checkpoint only advances when payment is confirmed. Alternatively, in `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, explicitly restore the original checkpoint values on `pos` before calling `setPositionWithState`. [4](#0-3) 

### Proof of Concept

1. User calls `MsgLockTier` → position created, delegated to validator A.
2. User calls `MsgTierRedelegate` to validator B → redelegation entry created, `RedelegationMappings[unbondingId] = positionId`.
3. Time passes; bonus accrues (e.g., 30 days × 4% APY).
4. Bonus pool is drained to near-zero by other claimants.
5. Validator B is slashed → staking module fires `BeforeRedelegationSlashed(unbondingId, sharesToUnbond)`.
6. `slashRedelegationPosition` is called → `processEventsAndClaimBonus` runs:
   - `applyBonusAccrualCheckpoint` advances `pos.LastBonusAccrual` to `blockTime`.
   - Pool check fails → returns `ErrInsufficientBonusPool`.
7. Error is swallowed; `setPositionWithState(ctx, pos, nil)` persists `LastBonusAccrual = blockTime`.
8. Pool is later replenished. User calls `MsgClaimTierRewards`.
9. `processEventsAndClaimBonus` starts from `LastBonusAccrual = blockTime` — the 30-day accrual window is gone. User receives zero bonus for that period. [5](#0-4) [6](#0-5)

### Citations

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

**File:** x/tieredrewards/keeper/slash.go (L19-77)
```go
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
