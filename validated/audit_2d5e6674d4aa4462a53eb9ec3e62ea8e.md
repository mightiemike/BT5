### Title
Unbounded Validator Event Iteration in `processEventsAndClaimBonus` Can Permanently Lock Accrued Bonus Rewards - (File: x/tieredrewards/keeper/claim_rewards.go)

### Summary
`processEventsAndClaimBonus` calls `getValidatorEventsSince` with no upper bound on the number of events returned. If a position has not claimed for a long time and the validator has accumulated many lifecycle events (slash/bond/unbond), the full event list is loaded into memory and iterated in a single transaction. If the event count is large enough to exceed the block gas limit, the claim transaction will always fail, permanently locking the position's accrued bonus rewards with no recovery path.

### Finding Description
`getValidatorEventsSince` performs an unbounded KV-store range scan, collecting every `ValidatorEvent` with sequence number greater than `pos.LastEventSeq` into a slice with no pagination or cap:

```go
func (k Keeper) getValidatorEventsSince(...) ([]EventEntry, error) {
    rng := collections.NewPrefixedPairRange[sdk.ValAddress, uint64](valAddr).
        StartExclusive(startSeq)
    var entries []EventEntry
    err := k.ValidatorEvents.Walk(ctx, rng, func(...) (bool, error) {
        entries = append(entries, EventEntry{...})
        return false, nil   // never stops early
    })
    ...
}
``` [1](#0-0) 

The caller `processEventsAndClaimBonus` then iterates over the entire returned slice, performing a store write (`decrementEventRefCount`) on every entry:

```go
for _, entry := range events {
    ...
    pos.UpdateLastEventSeq(entry.Seq)
    if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
        return nil, err
    }
}
``` [2](#0-1) 

This function is called from every reward-claim path: `claimRewards`, `claimRewardsAndUpdatesPositions`, and `claimRewardsAndUpdateTierPositions`. [3](#0-2) 

Events accumulate in the store for a position as long as it has not claimed. The `ReferenceCount` mechanism only garbage-collects an event once **all** positions that need it have processed it. A single unclaimed position keeps every event since its `LastEventSeq` alive in the store indefinitely. [4](#0-3) 

Validator lifecycle events are appended by three staking hooks — `BeforeValidatorSlashed`, `AfterValidatorBeginUnbonding`, and `AfterValidatorBonded` — each time the corresponding event fires for a validator that has at least one tier position. [5](#0-4) 

There is no mechanism to process events in batches across multiple transactions. `pos.LastEventSeq` only advances inside `processEventsAndClaimBonus`, so a partial run is impossible: the position either processes all pending events in one call or none at all.

### Impact Explanation
If the accumulated event count for a position exceeds what can be processed within the block gas limit, every `MsgClaimTierRewards`, `MsgClearPosition`, `MsgTierRedelegate`, and `MsgAddToTierPosition` call for that position will fail with out-of-gas. Because there is no partial-processing path, the position's accrued bonus rewards become permanently inaccessible. The locked principal (staked tokens) is unaffected, but all bonus rewards earned since the last claim are lost.

The corrupted value is the position owner's accrued bonus reward balance, which can never be transferred from the `RewardsPoolName` module account to the owner. [6](#0-5) 

### Likelihood Explanation
Likelihood is low-to-medium. A validator must accumulate a large number of slash/bond/unbond events while a position remains unclaimed. In a production network with active jailing, unjailing, and slashing activity, a validator could accumulate hundreds of events over years. A position owner who locks tokens and does not interact with the chain for an extended period (e.g., lost key access, forgotten position) would be the primary victim. The `MaxClaimPositionIds = 300` limit on `MsgClaimTierRewards` does not help because the bottleneck is events-per-position, not positions-per-message. [7](#0-6) 

### Recommendation
Cap the number of events processed per call. Introduce a `MaxEventsPerClaim` constant and stop the walk early once the limit is reached, returning a sentinel error or a partial-processing flag so the caller can retry. Alternatively, add a `StartExclusive`/`EndExclusive` range parameter to `getValidatorEventsSince` and expose a paginated claim path that advances `LastEventSeq` incrementally across multiple transactions.

### Proof of Concept
1. Deploy the chain and create a tier position on validator V.
2. Repeatedly jail and unjail V (or trigger slashes) to generate a large number of `ValidatorEvent` entries for V. Each jail/unjail cycle appends one UNBOND event and one BOND event via `AfterValidatorBeginUnbonding` / `AfterValidatorBonded`.
3. Do not call `MsgClaimTierRewards` for the position during this period. All events accumulate in the store with `ReferenceCount ≥ 1` because the position's `LastEventSeq` has not advanced.
4. After N events (where N × gas-per-iteration > block gas limit), submit `MsgClaimTierRewards` for the position. `getValidatorEventsSince` returns all N entries; the loop in `processEventsAndClaimBonus` exhausts the gas budget and the transaction fails.
5. No subsequent claim transaction can succeed because the same N events must be replayed from `LastEventSeq` every time. [8](#0-7) [9](#0-8)

### Citations

**File:** x/tieredrewards/keeper/validator_events.go (L65-81)
```go
// getValidatorEventsSince returns all events for a validator with sequence > startSeq,
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

**File:** x/tieredrewards/keeper/validator_events.go (L83-101)
```go
// decrementEventRefCount decrements the reference count of a validator event.
// If the reference count reaches zero, the event is deleted.
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L87-103)
```go
func (k Keeper) claimRewards(ctx context.Context, pos types.PositionState) (types.PositionState, sdk.Coins, sdk.Coins, error) {
	if !pos.IsDelegated() {
		return pos, sdk.NewCoins(), sdk.NewCoins(), nil
	}

	base, err := k.claimBaseRewards(ctx, pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	return pos, base, bonus, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-199)
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-241)
```go
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

**File:** x/tieredrewards/keeper/hooks.go (L27-75)
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

// AfterValidatorBonded records a BOND event.
func (h Hooks) AfterValidatorBonded(ctx context.Context, _ sdk.ConsAddress, valAddr sdk.ValAddress) error {
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
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
	return err
```

**File:** x/tieredrewards/types/msgs.go (L10-12)
```go
// MaxClaimPositionIds is the maximum number of position IDs that can be
// claimed in a single MsgClaimTierRewards transaction.
const MaxClaimPositionIds = 300
```
