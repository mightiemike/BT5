### Title
Accrued Bonus Rewards Permanently Lost When Bonus Pool Is Insufficient During Redelegation Slash - (File: `x/tieredrewards/keeper/slash.go`)

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is deliberately swallowed to prevent a chain halt. However, the position's in-memory accrual checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) have already been advanced by `processEventsAndClaimBonus` before the pool check, and the modified position is then persisted via `setPositionWithState`. The accrual period is consumed without the bonus ever being paid, making the loss permanent and irrecoverable.

### Finding Description

`processEventsAndClaimBonus` modifies the `pos` object in-memory — advancing `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded`, and decrementing event reference counts in the store — **before** it checks pool sufficiency: [1](#0-0) 

The pool check and payment happen only after all checkpoint mutations: [2](#0-1) 

When `ErrInsufficientBonusPool` is returned, `slashRedelegationPosition` swallows it: [3](#0-2) 

Execution then falls through to `setPositionWithState`, which persists the already-mutated `pos` (with advanced checkpoints) to the store: [4](#0-3) 

Because `LastBonusAccrual` and `LastEventSeq` are now advanced past the accrual period, the next call to `processEventsAndClaimBonus` for this position will compute zero bonus for the already-consumed window. The bonus is gone with no mechanism to recover it.

### Impact Explanation

The exact corrupted value is the position owner's accrued bonus reward — specifically, all bonus computed for the segment `[pos.LastBonusAccrual, slash_block_time]` using pre-slash shares. This amount is permanently lost: the pool retains the tokens (they are not transferred), but the position's checkpoints have moved past the period, so the owner can never claim those rewards. This is a direct, irreversible user fund loss.

### Likelihood Explanation

The trigger requires two concurrent conditions:

1. A tier position has an active redelegation (i.e., `MsgTierRedelegate` was submitted and the redelegation has not yet matured).
2. The bonus pool balance is less than the accrued bonus at the moment the validator slash fires.

Both conditions are realistic in normal protocol operation. The bonus pool can be drained by other users legitimately claiming rewards. Validator downtime slashes are routine Cosmos SDK events. The position owner has no control over when the slash fires or the pool balance at that moment. The `BeforeRedelegationSlashed` hook is triggered automatically by the staking module — no privileged action is required.

### Recommendation

In `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, the position's in-memory checkpoints must **not** be persisted in their advanced state. Options:

1. **Snapshot and restore**: Before calling `processEventsAndClaimBonus`, snapshot the relevant checkpoint fields (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`). On `ErrInsufficientBonusPool`, restore the snapshot before calling `setPositionWithState`, so the next claim can re-compute the same period once the pool is replenished.
2. **Deferred bookkeeping**: Record the "owed but unpaid" bonus amount in a separate store entry and pay it out when the pool is next funded (analogous to the external report's recommendation).

Additionally, the event reference count decrements that occur inside `processEventsAndClaimBonus` before the pool check should be guarded or rolled back on pool failure, to avoid premature garbage-collection of validator events.

### Proof of Concept

1. Alice creates a tier position on validator V1 and redelegates it to V2 via `MsgTierRedelegate`. A `RedelegationMappings[unbondingId → positionId]` entry is created.
2. Time passes; Alice accrues, say, 500 bonus tokens.
3. The bonus pool is drained to 0 by other users claiming rewards.
4. V1 goes offline and is slashed for downtime. The Cosmos SDK staking module calls `BeforeRedelegationSlashed(ctx, unbondingId, sharesToUnbond)`.
5. `slashRedelegationPosition` is invoked. `processEventsAndClaimBonus` runs, advances `pos.LastBonusAccrual` to the current block time and `pos.LastEventSeq` past all pending events, then returns `ErrInsufficientBonusPool` because the pool is empty.
6. The error is swallowed. `setPositionWithState` persists the position with the advanced checkpoints.
7. The pool is later refunded. Alice calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` computes zero bonus for the already-consumed window. The 500 tokens remain in the pool, permanently unclaimable by Alice. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-232)
```go
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

**File:** x/tieredrewards/keeper/slash.go (L53-77)
```go
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

**File:** x/tieredrewards/keeper/hooks.go (L125-130)
```go
// BeforeRedelegationSlashed fires before SDK's Unbond in SlashRedelegation.
// Routes to slashRedelegationPosition via the unbondingId → positionId mapping
// so bonus settlement can run against pre-slash shares.
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```
