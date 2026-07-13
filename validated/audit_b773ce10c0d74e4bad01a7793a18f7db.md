### Title
Unbounded Validator Event Iteration in `processEventsAndClaimBonus` Can Exceed Gas Limit, Permanently Locking Position Funds — (File: `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

The `processEventsAndClaimBonus` function in `x/tieredrewards/keeper/claim_rewards.go` fetches and iterates over **all** validator events since a position's `LastEventSeq` with no upper bound. If a validator accumulates a large number of bond/unbond/slash events over its lifetime, any position holder delegated to that validator will find their claim-rewards or exit-position transaction exceeding the block gas limit. Because the same loop is a required step in the exit flow, the position holder's staked funds become permanently locked with no recovery path.

---

### Finding Description

`processEventsAndClaimBonus` is the core bonus-accounting function. It calls `getValidatorEventsSince`, which walks the entire `ValidatorEvents` KV range from `pos.LastEventSeq + 1` to the latest event with no pagination or cap: [1](#0-0) 

The returned slice is then iterated in full inside `processEventsAndClaimBonus`: [2](#0-1) 

Each loop iteration performs:
1. A `computeSegmentBonus` arithmetic computation.
2. A KV store read + conditional write via `decrementEventRefCount`.

Events are appended to the `ValidatorEvents` store by three staking hooks — `AfterValidatorBeginUnbonding`, `AfterValidatorBonded`, and `BeforeValidatorSlashed`: [3](#0-2) [4](#0-3) 

Events are **not** deleted until every position that was delegated to the validator at the time of the event has processed it (reference-count reaches zero): [5](#0-4) 

A position that has not claimed rewards since creation will accumulate the full history of validator events. There is no cap on how many events a validator can accumulate, and there is no batching mechanism in `processEventsAndClaimBonus`.

The same function is invoked from both the single-position claim path and the bulk tier-claim path: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

A position holder whose validator has accumulated `N` events since the position's `LastEventSeq` must process all `N` events in a single transaction. Once `N` is large enough to push the transaction past the block gas limit, every attempt to claim rewards or exit the position will revert. Because the exit flow requires `processEventsAndClaimBonus` to complete successfully, the position's staked tokens — held in the position's dedicated delegator account — are permanently locked. Neither the owner nor any governance action can recover them without a chain upgrade.

The exact corrupted value is the staked `basecro` balance held in the position's `DelegatorAddress` account, which can no longer be undelegated or withdrawn.

---

### Likelihood Explanation

Validators on a live Cosmos POS chain routinely cycle between bonded and unbonded status as their voting power rank changes (entering/leaving the active set), and they can be slashed for downtime or double-signing. A validator that has been running for months or years can easily accumulate hundreds of such events. A position created early in the validator's life and left unclaimed will silently accumulate all of them. No attacker action is required; normal validator lifecycle events are sufficient. A malicious actor could also accelerate accumulation by repeatedly triggering jailing/unjailing of a validator they control, targeting positions delegated to it.

---

### Recommendation

1. **Cap the number of events processed per transaction.** Introduce a `maxEventsPerClaim` parameter and process at most that many events per call, advancing `LastEventSeq` incrementally so the user can call again to continue.
2. **Add a hard cap on validator event retention.** Limit the total number of events stored per validator (e.g., 1000). Positions that fall too far behind should be force-exited or have their bonus forfeited beyond the cap.
3. **Expose a paginated claim endpoint** so users can drain their event backlog in multiple transactions rather than one.

---

### Proof of Concept

1. Create a tiered-rewards position delegated to validator `V`.
2. Trigger 500+ bond/unbond/slash cycles on `V` (e.g., via repeated jailing and unjailing, or by having `V` repeatedly enter and leave the active set). Each cycle appends one or more entries to `ValidatorEvents` for `V`.
3. Do **not** claim rewards during this period, so `pos.LastEventSeq` remains at its initial value.
4. Submit a `MsgClaimRewards` or exit transaction for the position.
5. Observe that `processEventsAndClaimBonus` → `getValidatorEventsSince` returns all 500+ events, the iteration loop exhausts the block gas limit, and the transaction reverts.
6. Repeat step 4 indefinitely — every attempt reverts, and the staked funds in the position's delegator account are permanently inaccessible.

The root cause is the unbounded `Walk` in `getValidatorEventsSince` at `x/tieredrewards/keeper/validator_events.go:69-76` and the unconditional full iteration in `processEventsAndClaimBonus` at `x/tieredrewards/keeper/claim_rewards.go:172-198`, with no pagination, cap, or batching anywhere in the call chain. [8](#0-7) [1](#0-0)

### Citations

**File:** x/tieredrewards/keeper/validator_events.go (L67-81)
```go
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L59-77)
```go
	for _, id := range ids {
		pos, err := k.getPositionState(ctx, id)
		if err != nil {
			return err
		}
		if !pos.IsDelegated() {
			continue
		}

		if _, err := k.claimBaseRewards(ctx, pos); err != nil {
			return err
		}
		if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
			return err
		}
		if err := k.setPosition(ctx, pos.Position, nil); err != nil {
			return err
		}
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L110-134)
```go
	for i := range positions {
		pos := &positions[i]

		if !pos.IsDelegated() {
			continue
		}

		base, err := k.claimBaseRewards(ctx, *pos)
		if err != nil {
			return nil, nil, err
		}
		totalBase = totalBase.Add(base...)

		bonus, err := k.processEventsAndClaimBonus(ctx, pos)
		if err != nil {
			return nil, nil, err
		}
		totalBonus = totalBonus.Add(bonus...)

		if err := k.setPosition(ctx, pos.Position, nil); err != nil {
			return nil, nil, err
		}
	}

	return totalBase, totalBonus, nil
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-200)
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

```

**File:** x/tieredrewards/keeper/hooks.go (L27-50)
```go
func (h Hooks) AfterValidatorBeginUnbonding(ctx context.Context, _ sdk.ConsAddress, valAddr sdk.ValAddress) error {
	count, err := h.k.getPositionCountForValidator(ctx, valAddr)
	if err != nil {
		return err
	}
	if count == 0 {
		return nil
	}

	tokensPerShare, err := h.k.getTokensPerShare(ctx, valAddr)
	if err != nil {
		return err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	_, err = h.k.appendValidatorEvent(ctx, valAddr, types.ValidatorEvent{
		Height:         sdkCtx.BlockHeight(),
		Timestamp:      sdkCtx.BlockTime(),
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
	return err
}
```

**File:** x/tieredrewards/keeper/hooks.go (L97-123)
```go
// BeforeValidatorSlashed records a SLASH event.
// The distribution module handles slash accounting for all delegators
// (including the tier module pool) via ValidatorSlashEvent records.
func (h Hooks) BeforeValidatorSlashed(ctx context.Context, valAddr sdk.ValAddress, fraction sdkmath.LegacyDec) error {
	count, err := h.k.getPositionCountForValidator(ctx, valAddr)
	if err != nil {
		return err
	}
	if count == 0 {
		return nil
	}

	tokensPerShare, err := h.k.getTokensPerShare(ctx, valAddr)
	if err != nil {
		return err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	_, err = h.k.appendValidatorEvent(ctx, valAddr, types.ValidatorEvent{
		Height:         sdkCtx.BlockHeight(),
		Timestamp:      sdkCtx.BlockTime(),
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_SLASH,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
	return err
}
```
