### Title
Duplicate Position IDs in `MsgClaimTierRewards` Enable Double-Spending of Bonus Rewards — (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`ClaimTierRewards` accepts a caller-supplied list of position IDs and claims rewards for each. There is no deduplication check on that list. Submitting the same position ID twice causes `processEventsAndClaimBonus` to execute twice against the same stale in-memory position state, sending bonus coins from the `RewardsPoolName` module account to the owner twice while the on-chain position record ends up looking as if only one claim occurred.

---

### Finding Description

`ClaimTierRewards` in `msg_server.go` builds a `positions` slice by iterating over `msg.PositionIds` without checking for duplicates: [1](#0-0) 

Each position is loaded from the store into a **local copy** and appended to the slice. If the same ID appears twice, two independent copies of the same position state are appended.

`claimRewardsAndUpdatesPositions` then iterates over that slice: [2](#0-1) 

For each entry it calls `processEventsAndClaimBonus`, which:
1. Reads validator events since `pos.LastEventSeq` (the **old** value, identical in both copies).
2. Computes bonus for each bonded segment.
3. Calls `k.bankKeeper.SendCoinsFromModuleToAccount` from `RewardsPoolName` to the owner.
4. Calls `k.decrementEventRefCount` for every processed event.
5. Advances `pos.LastEventSeq` and `pos.LastBonusAccrual` **only in the local slice entry**. [3](#0-2) 

After the first iteration, `setPosition` writes the updated position back to the store. The second iteration's slice entry still holds the **original stale state** (old `LastEventSeq`, old `LastBonusAccrual`), so `processEventsAndClaimBonus` re-processes the same event window and issues a second `SendCoinsFromModuleToAccount` transfer. The `sufficientBonusPoolBalance` guard only prevents the attack when the pool is already nearly empty; it does not prevent the duplicate payment itself. [4](#0-3) 

Additionally, `decrementEventRefCount` is called twice for the same events, which can corrupt the reference-count bookkeeping for validator events.

---

### Impact Explanation

The exact corrupted value is the `RewardsPoolName` module account balance. An owner with a single delegated position can drain the bonus pool at N× the legitimate rate by including the same position ID N times in one `MsgClaimTierRewards` transaction. The position record in the store is left in a state that appears consistent (checkpoints are advanced), so the over-payment is not detectable from the position alone.

---

### Likelihood Explanation

Any position owner can craft this transaction with no special privileges. The Cosmos SDK ante handler does not deduplicate entries within a repeated field of a single message. The attack requires only a funded position and knowledge of its ID, both of which are publicly observable on-chain.

---

### Recommendation

Add a duplicate-ID check before building the `positions` slice in `ClaimTierRewards`:

```go
seen := make(map[uint64]struct{}, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    if _, ok := seen[posId]; ok {
        return nil, errorsmod.Wrapf(types.ErrInvalidPositionID, "duplicate position id %d", posId)
    }
    seen[posId] = struct{}{}
    // ... existing load + validate logic
}
```

Alternatively, enforce uniqueness in `MsgClaimTierRewards.Validate()` so the check happens at the message-validation layer before any state is touched.

---

### Proof of Concept

1. Owner creates a delegated position; its ID is `42`.
2. Owner waits for bonus rewards to accrue.
3. Owner submits `MsgClaimTierRewards{Owner: owner, PositionIds: [42, 42]}`.
4. `ClaimTierRewards` loads position `42` twice, appending two identical `PositionState` copies to `positions`.
5. First loop iteration: `processEventsAndClaimBonus` computes bonus `B` and sends `B` coins from `RewardsPoolName` to owner; `setPosition` writes the advanced checkpoint to the store.
6. Second loop iteration: the second slice entry still has the old `LastEventSeq`; `processEventsAndClaimBonus` re-computes the same bonus `B` and sends another `B` coins from `RewardsPoolName` to owner.
7. Owner receives `2B` bonus coins; the `RewardsPoolName` module account is debited `2B`; the position record in the store reflects only one claim. [5](#0-4)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-251)
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

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}

	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventBonusRewardsClaimed{
		PositionId: pos.Id,
		Owner:      pos.Owner,
		Rewards:    bonusCoins,
	}); err != nil {
		return nil, err
	}

	return bonusCoins, nil
```
