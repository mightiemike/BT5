### Title
Premature Bonus Checkpoint Advancement on `ErrInsufficientBonusPool` in `BeforeRedelegationSlashed` Permanently Loses Accrued Bonus Rewards - (File: `x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is deliberately swallowed to prevent a chain halt. However, by the time the error is returned, `processEventsAndClaimBonus` has already advanced the position's `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded` checkpoints in memory, and has decremented (and potentially garbage-collected) event reference counts in the store. The mutated position is then persisted via `setPositionWithState`. All future claims by the position owner will start from the advanced `LastEventSeq`, permanently skipping the events that were processed but never paid out.

---

### Finding Description

`slashRedelegationPosition` in `x/tieredrewards/keeper/slash.go` calls `processEventsAndClaimBonus` and swallows `ErrInsufficientBonusPool`:

```go
// slash.go lines 54–64
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
    } else {
        return err
    }
}
// ... continues to setPositionWithState with the mutated pos
```

Inside `processEventsAndClaimBonus` (`claim_rewards.go`), the ordering of operations is:

1. **Lines 172–198**: For each pending event, `pos.UpdateLastEventSeq(entry.Seq)` and `k.decrementEventRefCount(ctx, valAddr, entry.Seq)` are called — advancing the sequence pointer and decrementing (possibly deleting) the event from the store.
2. **Line 215**: `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` — advances `LastBonusAccrual` to the current block time.
3. **Line 217**: `pos.UpdateLastKnownBonded(bonded)` — updates the bonded state.
4. **Line 230**: `k.sufficientBonusPoolBalance(ctx, bonusCoins)` — **only here** is the pool balance checked. If insufficient, the error is returned.

All three checkpoints and all event reference-count decrements happen **before** the pool balance check. When the pool is insufficient, the function returns an error with the in-memory `pos` already fully mutated and the store already modified (reference counts decremented, events possibly deleted).

Back in `slashRedelegationPosition`, after swallowing the error, execution continues to either line 71 or line 77, both of which call `k.setPositionWithState(ctx, pos, ...)`, persisting the mutated position with its advanced checkpoints. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

---

### Impact Explanation

The corrupted state values are:

- **`pos.LastEventSeq`**: Advanced past events whose bonus was computed but never paid. All future calls to `processEventsAndClaimBonus` for this position call `getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)`, which starts **after** the already-advanced sequence, permanently skipping those events.
- **`pos.LastBonusAccrual`**: Advanced to `blockTime`. The time window `[old_LastBonusAccrual, blockTime]` is permanently consumed with no payment.
- **`pos.LastKnownBonded`**: Updated to reflect post-event bonded state, so the next claim's segment computation starts from the wrong bonded baseline.
- **Event `ReferenceCount`**: Decremented in the store (line 196). If a reference count reaches zero, the event is deleted from `ValidatorEvents`. Even if the position's checkpoints were somehow reset, the event data is gone.

The net result is **permanent, unrecoverable loss of accrued bonus rewards** for the affected position owner. The amount lost equals the bonus that was computed for the interval `[LastBonusAccrual, blockTime]` across all pending events — which can span an arbitrarily long bonded period if the position has not claimed recently. [6](#0-5) [7](#0-6) 

---

### Likelihood Explanation

The trigger requires two concurrent conditions:

1. **A redelegation slash fires**: A validator with active tier-position redelegations is slashed (double-sign evidence or downtime). This is a normal, unprivileged protocol event — no attacker control is needed. Any validator jailing/slashing event during an active redelegation window suffices.
2. **The bonus pool is insufficient**: The `RewardsPoolName` module account balance is below the computed bonus. The pool is a finite, governance-funded account. It can be depleted by normal claim activity, or it may simply not have been replenished. An adversary who monitors pool balance and times a slash (e.g., by submitting double-sign evidence) when the pool is low can reliably trigger this.

Both conditions are realistic in production. The pool being empty is explicitly acknowledged as a handled case in the ADR ("Pool empty (user-driven): Message fails atomically. No state change. Retry after pool replenished."), but the hook path does not provide the same atomicity guarantee. [8](#0-7) 

---

### Recommendation

Move all checkpoint mutations and reference-count decrements to **after** the pool balance check succeeds. Specifically, restructure `processEventsAndClaimBonus` so that:

1. The event loop computes `totalBonus` and accumulates the new checkpoint values in local variables (not yet written to `pos` or the store).
2. The pool balance check is performed against `totalBonus`.
3. Only if the check passes are `pos.UpdateLastEventSeq`, `decrementEventRefCount`, `applyBonusAccrualCheckpoint`, and `UpdateLastKnownBonded` applied.

Alternatively, in `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, explicitly roll back the in-memory `pos` to its pre-call state (re-read from store) before calling `setPositionWithState`, so that the persisted position retains its original checkpoints and the owner can retry the claim later. [9](#0-8) [10](#0-9) 

---

### Proof of Concept

1. A position owner creates a tier position on validator V and accrues bonus over 90 days (large `totalBonus`).
2. The owner redelegates the position from V to V2 via `MsgTierRedelegate`. A `RedelegationMappings` entry is created.
3. The bonus pool is drained to near-zero (e.g., by other position owners claiming rewards).
4. Validator V is slashed (e.g., double-sign evidence submitted). The staking module fires `BeforeRedelegationSlashed` → `slashRedelegationPosition`.
5. Inside `processEventsAndClaimBonus`:
   - All pending events for V are walked; `pos.LastEventSeq` is advanced to the latest seq; each event's `ReferenceCount` is decremented (events deleted from store).
   - `pos.LastBonusAccrual` is set to `blockTime`.
   - `sufficientBonusPoolBalance` fails → `ErrInsufficientBonusPool` returned.
6. Back in `slashRedelegationPosition`, the error is swallowed. `setPositionWithState` persists the mutated `pos`.
7. The owner later calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` calls `getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)` — returns empty (all events already deleted). `totalBonus = 0`. No payment. The 90-day accrued bonus is permanently lost. [11](#0-10) [12](#0-11) [13](#0-12)

### Citations

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

**File:** x/tieredrewards/keeper/validator_events.go (L66-81)
```go
// in ascending order.
func (k Keeper) getValidatorEventsSince(ctx context.Context, valAddr sdk.ValAddress, startSeq uint64) ([]EventEntry, error) {
	// Range from (valAddr, startSeq+1) to end of valAddr prefix.
	rng := collections.NewPrefixedPairRange[sdk.ValAddress, uint64](valAddr).
		StartExclusive(startSeq)

	var entries []EventEntry
	err := k.ValidatorEvents.Walk(ctx, rng, func(key collections.Pair[sdk.ValAddress, uint64], event types.ValidatorEvent) (bool, error) {
		entries = append(entries, EventEntry{Seq: key.K2(), Event: event})
		return false, nil
	})
	if err != nil {
		return nil, err
	}
	return entries, nil
}
```

**File:** x/tieredrewards/keeper/validator_events.go (L85-101)
```go
func (k Keeper) decrementEventRefCount(ctx context.Context, valAddr sdk.ValAddress, seq uint64) error {
	key := collections.Join(valAddr, seq)
	event, err := k.ValidatorEvents.Get(ctx, key)
	if errors.Is(err, collections.ErrNotFound) {
		return nil // already cleaned up
	}
	if err != nil {
		return err
	}

	if event.ReferenceCount <= 1 {
		return k.ValidatorEvents.Remove(ctx, key)
	}

	event.ReferenceCount--
	return k.ValidatorEvents.Set(ctx, key, event)
}
```

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```
