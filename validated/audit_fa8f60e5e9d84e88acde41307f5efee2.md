### Title
Permanent Bonus Reward Loss via Checkpoint Advancement Before Pool Sufficiency Check in `slashRedelegationPosition` - (File: `x/tieredrewards/keeper/slash.go`)

### Summary

In `x/tieredrewards/keeper/slash.go`, the `slashRedelegationPosition` function (called from the `BeforeRedelegationSlashed` staking hook) silently swallows `ErrInsufficientBonusPool` from `processEventsAndClaimBonus`. Because `processEventsAndClaimBonus` advances the position's bonus checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) **in memory via pointer mutation before** checking pool sufficiency, and `slashRedelegationPosition` then persists those advanced checkpoints via `setPositionWithState`, the user permanently loses all bonus rewards accrued since their last claim. There is no rescue or recovery path.

### Finding Description

`processEventsAndClaimBonus` takes `pos *types.PositionState` by pointer. Its execution order is:

1. Walk validator events, decrement reference counts, advance `pos.LastEventSeq` (store writes).
2. Call `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` — advances `pos.LastBonusAccrual` in memory.
3. Call `pos.UpdateLastKnownBonded(bonded)` — updates bonded state in memory.
4. **Only then** call `sufficientBonusPoolBalance` — if the pool is short, return `nil, ErrInsufficientBonusPool`. [1](#0-0) 

Because steps 1–3 mutate `pos` through the pointer before step 4 returns the error, the caller's `pos` variable already holds the advanced checkpoints when the error surfaces.

In `slashRedelegationPosition`, the error is explicitly caught and execution continues:

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // execution falls through — pos has advanced checkpoints, no bonus paid
    } else {
        return err
    }
}
``` [2](#0-1) 

For a **partial slash**, the function then calls `setPositionWithState` with the already-mutated `pos`: [3](#0-2) 

This persists `LastBonusAccrual = blockTime`, `LastEventSeq = latestSeq`, and the updated `LastKnownBonded` — permanently closing the accrual window for the period during which the pool was insufficient. The user can never reclaim those rewards because the checkpoints have advanced past the accrual period.

A secondary effect: the `decrementEventRefCount` store writes inside the event loop (line 196) are also committed even though no bonus was paid, potentially causing premature garbage collection of validator events. [4](#0-3) 

There is no governance message, admin function, or user-callable path to rescue the forfeited bonus. The `RewardsPoolName` module account retains the funds, but the user's claim to them is permanently erased. [5](#0-4) 

### Impact Explanation

A position owner who has a redelegating position at the time of a validator slash, when the bonus pool is empty or insufficient, permanently loses all bonus rewards accrued since their last claim. The `rewards_pool` module account retains the tokens, but no user can ever claim the forfeited amount for that accrual window. This is a direct, irreversible user fund loss (earned but unpaid bonus rewards).

**Impact: Medium** — affects only bonus rewards (not principal or base staking rewards), but the loss is permanent and unrecoverable.

### Likelihood Explanation

Requires two simultaneous conditions: (1) a tier position is in an active redelegation (21-day window), and (2) the `rewards_pool` balance is insufficient to cover the accrued bonus at the moment the slash fires. The pool can be empty if it was never funded, was drained by the `BeginBlocker` top-up mechanism, or governance set `TargetBaseRewardsRate` to zero and the pool was exhausted. Both conditions are independently plausible in production.

**Likelihood: Low** — the conjunction of a redelegation slash and an empty pool is uncommon but not theoretical.

### Recommendation

Move `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` to **after** the `sufficientBonusPoolBalance` check, so that checkpoints are only advanced when the bonus is actually paid. Alternatively, in `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, explicitly reset the in-memory checkpoint mutations on `pos` before calling `setPositionWithState`, so the position retains its pre-slash accrual state and the user can retry the claim once the pool is replenished. [6](#0-5) 

### Proof of Concept

1. User calls `MsgLockTier` to create a position on validator V1.
2. User calls `MsgTierRedelegate` to move the position to validator V2. A redelegation entry is created with `unbondingID = X`; `RedelegationMappings[X] = positionId` is stored.
3. Time passes; bonus accrues. The `rewards_pool` balance is zero (e.g., pool was drained by `BeginBlocker` or never funded).
4. V1 is slashed for downtime. The Cosmos SDK calls `BeforeRedelegationSlashed(ctx, X, sharesToUnbond)` where `sharesToUnbond < pos.Delegation.Shares` (partial slash).
5. `slashRedelegationPosition` calls `processEventsAndClaimBonus(ctx, &pos)`.
6. Inside `processEventsAndClaimBonus`: event loop runs, reference counts are decremented, `pos.LastBonusAccrual` is set to `blockTime`, `pos.LastEventSeq` is advanced. Then `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`.
7. Back in `slashRedelegationPosition`: the error is caught, execution continues. `setPositionWithState(ctx, pos, nil)` persists the position with `LastBonusAccrual = blockTime`.
8. User later calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts from the new `LastBonusAccrual = blockTime` — the entire prior accrual window is gone. The user receives zero bonus for that period, permanently. [7](#0-6) [8](#0-7)

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

**File:** x/tieredrewards/types/keys.go (L20-27)
```go
const (
	ModuleName      = "tieredrewards"
	StoreKey        = ModuleName
	RewardsPoolName = "rewards_pool"

	// SecondsPerYear is 365.25 days, used to convert durations to years for bonus calculation.
	SecondsPerYear int64 = 31_557_600
)
```
