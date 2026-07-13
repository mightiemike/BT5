### Title
Bonus Rewards Permanently Lost on Partial Redelegation Slash When Pool Is Insufficient — (File: `x/tieredrewards/keeper/slash.go`)

### Summary
In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is swallowed to prevent chain halt. However, by the time the error surfaces, `processEventsAndClaimBonus` has already (1) decremented validator event reference counts in persistent KV state and (2) advanced the position's `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded` in-memory. The caller then persists those advanced checkpoints via `setPositionWithState`. The position's accounting state moves past the earned-but-unpaid bonus period, permanently destroying the owner's ability to claim those rewards even after the pool is replenished.

### Finding Description

`processEventsAndClaimBonus` performs two categories of work **before** the pool-sufficiency check:

**1. Persistent state changes — inside the event loop:** [1](#0-0) 

`pos.UpdateLastEventSeq(entry.Seq)` advances the in-memory checkpoint, and `k.decrementEventRefCount` decrements the event's reference count in the KV store. When the count reaches zero the event is garbage-collected.

**2. In-memory checkpoint updates — after the loop:** [2](#0-1) 

`applyBonusAccrualCheckpoint` sets `pos.LastBonusAccrual` to `blockTime`; `pos.UpdateLastKnownBonded` updates `pos.LastKnownBonded`.

**3. Pool check — last:** [3](#0-2) 

When this fails with `ErrInsufficientBonusPool`, the function returns with `pos` already mutated and event reference counts already decremented in state.

In `slashRedelegationPosition`, the error is caught and swallowed: [4](#0-3) 

For a **partial slash** (the common case), execution continues: [5](#0-4) 

`setPositionWithState` persists `pos`, which now carries the advanced `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded` from the failed call. The validator events whose reference counts were decremented are permanently consumed.

Net effect: the position's bonus accounting state advances to `blockTime` and past all pending events, but zero bonus is paid. If the pool is later replenished, the user has no retry path — the events are gone and the checkpoints have moved forward.

This is the direct analog to the external report's bug class: a fund-receipt path (`BeforeRedelegationSlashed`) advances the accounting state without delivering the corresponding reward, breaking the invariant that `LastBonusAccrual` and `LastEventSeq` only advance when rewards are actually paid.

### Impact Explanation

A position owner who holds a redelegating position at the moment the bonus pool is insufficient loses all bonus rewards accrued since their last claim. The loss is **permanent and irrecoverable**: the events are consumed, the checkpoints advance, and no retry path exists (unlike user-driven paths, which fail atomically with no state change). The corrupted value is the `bonus_rewards` amount that should have been transferred from the `RewardsPoolName` module account to the owner.

The `Position.LastBonusAccrual` field is the exact corrupted accounting value. [6](#0-5) 

**Impact: High** — permanent, irrecoverable loss of earned bonus rewards for affected position owners.

### Likelihood Explanation

The bonus pool is funded externally (governance). Pool exhaustion is a realistic operational condition during high-claim periods or delayed replenishment. Redelegation slashes occur whenever a validator misbehaves while a tier position has a pending redelegation entry. The combination is plausible in production.

**Likelihood: Low-Medium** — requires simultaneous pool exhaustion and a redelegation slash, but both are realistic operational events.

### Recommendation

In `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, **do not persist** the in-memory checkpoint updates from the failed `processEventsAndClaimBonus` call. Options:

1. Re-read the position from state before calling `setPositionWithState` to discard the in-memory mutations.
2. Refactor `processEventsAndClaimBonus` to separate checkpoint advancement and event consumption from the payment step, so checkpoints only advance after a successful payment.
3. Do not call `decrementEventRefCount` for events whose bonus will not be paid; keep reference counts intact so rewards can be claimed later.

### Proof of Concept

1. Alice creates a tier position and redelegates to validator B (`MsgTierRedelegate`). A `RedelegationMapping` entry is created.
2. The bonus pool is drained to zero (e.g., by many concurrent claims from other positions).
3. Validator B is slashed for misbehavior. The staking module calls `BeforeRedelegationSlashed` → `slashRedelegationPosition`.
4. Inside `slashRedelegationPosition`, `processEventsAndClaimBonus` runs: decrements event reference counts (persistent), advances `pos.LastEventSeq`/`LastBonusAccrual`/`LastKnownBonded` in-memory, then fails with `ErrInsufficientBonusPool`. No payment is made.
5. The error is swallowed. `setPositionWithState` persists the advanced checkpoints.
6. The pool is later replenished by governance.
7. Alice calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` finds no pending events (all consumed in step 4) and `LastBonusAccrual` is already at the slash block time. Zero bonus is paid.
8. Alice has permanently lost all bonus rewards accrued between her last claim and the slash. [7](#0-6) [8](#0-7)

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

**File:** x/tieredrewards/types/types.pb.go (L144-148)
```go
	// last_bonus_accrual is the last time bonus rewards was claimed.
	LastBonusAccrual time.Time `protobuf:"bytes,4,opt,name=last_bonus_accrual,json=lastBonusAccrual,proto3,stdtime" json:"last_bonus_accrual"`
	// last_event_seq is the sequence number of the last validator event this
	// position has processed. Events with seq > last_event_seq are pending.
	LastEventSeq uint64 `protobuf:"varint,5,opt,name=last_event_seq,json=lastEventSeq,proto3" json:"last_event_seq,omitempty"`
```
