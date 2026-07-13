### Title
Duplicate Position IDs in `MsgClaimTierRewards` Enable Double-Claiming of Bonus Rewards — (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

The `ClaimTierRewards` message handler loads all requested positions into an in-memory slice before processing. Because there is no deduplication of `msg.PositionIds`, an attacker can include the same position ID multiple times. Each duplicate entry carries the original pre-claim state, causing `processEventsAndClaimBonus` to re-process the same validator events and pay out bonus rewards a second time from the module's `RewardsPoolName` account.

---

### Finding Description

In `ClaimTierRewards`, positions are loaded from state into a slice before any rewards are claimed: [1](#0-0) 

Each call to `ms.getPositionState(ctx, posId)` reads the current on-chain state. If `posId` appears twice in `msg.PositionIds`, two independent in-memory copies of the same position are appended to `positions`, both holding the identical pre-claim `LastEventSeq` and `LastBonusAccrual` values.

These copies are then processed sequentially in `claimRewardsAndUpdatesPositions`: [2](#0-1) 

For each entry, `processEventsAndClaimBonus` is called with the in-memory `*pos`. It walks validator events since `pos.LastEventSeq`, computes bonus, transfers coins from `RewardsPoolName` to the owner, and decrements each event's reference count: [3](#0-2) 

After the first iteration, `setPosition` writes the updated position (with advanced `LastEventSeq`) back to the store. However, `positions[1]` — the duplicate — still holds the **original** `LastEventSeq`. When the second iteration runs `processEventsAndClaimBonus` on this stale copy, it re-fetches the same events (provided their reference count is still > 0) and issues a second `SendCoinsFromModuleToAccount` payout: [4](#0-3) 

The precondition for the events to survive the first iteration's `decrementEventRefCount` is that their reference count was ≥ 2 at recording time — i.e., at least two positions existed for the same validator when the event was appended: [5](#0-4) 

This is a routine condition: any user who opens two positions against the same validator satisfies it.

---

### Impact Explanation

1. **Bonus pool drain**: The attacker receives the same bonus payout twice (or N times with N duplicates, bounded by the event reference count) in a single transaction, directly withdrawing funds from the `RewardsPoolName` module account that belong to all stakers.
2. **Destruction of other positions' claims**: Each duplicate iteration decrements the event reference count. When the count reaches zero the event record is deleted. Any other position that has not yet claimed that event loses its accrued bonus permanently — a theft of other users' rewards.

The corrupted value is the `RewardsPoolName` module account balance and the per-validator event reference counts stored in the tiered-rewards keeper.

---

### Likelihood Explanation

The attack requires only:
- Owning at least two tiered-reward positions delegated to the same validator (a normal usage pattern explicitly supported by the module).
- Submitting a single `MsgClaimTierRewards` transaction with a repeated position ID.

No privileged role, leaked key, or social engineering is needed. The entry path is a standard, unprivileged Cosmos SDK transaction.

---

### Recommendation

Add a deduplication check on `msg.PositionIds` before the load loop in `ClaimTierRewards`. A simple approach is to build a `map[uint64]struct{}` and return an error if any ID appears more than once. Alternatively, enforce uniqueness in `MsgClaimTierRewards.Validate()` so the check is applied at the ante-handler level before the message reaches the keeper. [6](#0-5) 

---

### Proof of Concept

1. Open two tiered-reward positions `P1` and `P2` both delegated to validator `V`. This causes all subsequent validator events for `V` to be recorded with `ReferenceCount = 2`.
2. Wait for at least one validator event (bond/unbond/slash) to accumulate bonus for `P1`.
3. Submit `MsgClaimTierRewards{ Owner: attacker, PositionIds: [P1, P1] }`.
4. **First iteration** processes `positions[0]` (original state of `P1`): claims bonus, decrements event ref count from 2 → 1, writes updated `P1` to store.
5. **Second iteration** processes `positions[1]` (stale copy of `P1` with original `LastEventSeq`): re-fetches the same event (ref count = 1 > 0), computes identical bonus, issues a second `SendCoinsFromModuleToAccount` payout, decrements ref count from 1 → 0 (event deleted).
6. The attacker receives double the bonus. `P2` can no longer claim its bonus for that event because the event record was deleted prematurely.

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L429-468)
```go
func (ms msgServer) ClaimTierRewards(ctx context.Context, msg *types.MsgClaimTierRewards) (*types.MsgClaimTierRewardsResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	positions := make([]types.PositionState, 0, len(msg.PositionIds))
	for _, posId := range msg.PositionIds {
		pos, err := ms.getPositionState(ctx, posId)
		if err != nil {
			return nil, err
		}

		if err := ms.validateClaimRewards(pos.Position, msg.Owner); err != nil {
			return nil, err
		}

		positions = append(positions, pos)
	}

	totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventTierRewardsClaimed{
		Owner:        msg.Owner,
		PositionIds:  msg.PositionIds,
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
	}); err != nil {
		return nil, err
	}

	return &types.MsgClaimTierRewardsResponse{
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
		PositionIds:  msg.PositionIds,
	}, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L106-135)
```go
func (k Keeper) claimRewardsAndUpdatesPositions(ctx context.Context, positions []types.PositionState) (sdk.Coins, sdk.Coins, error) {
	totalBase := sdk.NewCoins()
	totalBonus := sdk.NewCoins()

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-241)
```go
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

**File:** x/tieredrewards/keeper/hooks.go (L42-49)
```go
	_, err = h.k.appendValidatorEvent(ctx, valAddr, types.ValidatorEvent{
		Height:         sdkCtx.BlockHeight(),
		Timestamp:      sdkCtx.BlockTime(),
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
	return err
```
