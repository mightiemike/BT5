### Title
Duplicate Position IDs in `MsgClaimTierRewards` Cause Double Bonus Reward Payment — (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`ClaimTierRewards` accepts a caller-supplied list of position IDs without deduplication. When the same ID appears twice, the position is loaded twice into the `positions` slice with identical initial state. The second copy retains the pre-claim `LastBonusAccrual` and `LastEventSeq` checkpoints, so `processEventsAndClaimBonus` computes and transfers the current-segment bonus a second time, draining the rewards pool by twice the legitimate amount.

---

### Finding Description

`ClaimTierRewards` in `msg_server.go` collects positions from `msg.PositionIds` in a plain loop with no duplicate check: [1](#0-0) 

All positions are loaded into a slice before any reward processing begins. The slice is then passed to `claimRewardsAndUpdatesPositions`: [2](#0-1) 

Inside `claimRewardsAndUpdatesPositions`, each element of the slice is processed independently via a pointer to its own slice index: [3](#0-2) 

When the same position ID appears at index `i` and index `j`, both slice elements hold the same initial `LastBonusAccrual` and `LastEventSeq` values. Processing index `i` calls `processEventsAndClaimBonus`, which:

1. Walks validator events since `pos.LastEventSeq` and computes segment bonuses.
2. Computes the **current-segment bonus** from `pos.LastBonusAccrual` to `blockTime` when the validator is still bonded.
3. Sends the bonus coins from the rewards pool to the owner.
4. Advances `pos.LastBonusAccrual` and `pos.LastEventSeq` on the **local slice element** and calls `setPosition` to persist the update. [4](#0-3) 

When index `j` (the duplicate) is processed next, its `LastBonusAccrual` and `LastEventSeq` are still the **pre-claim values** (the update at index `i` did not propagate to index `j`). `processEventsAndClaimBonus` therefore recomputes the current-segment bonus over the same time window and issues a second `SendCoinsFromModuleToAccount` transfer: [5](#0-4) 

The `applyBonusAccrualCheckpoint` call that would have advanced the checkpoint only affects the local copy at index `i`; the copy at index `j` is untouched: [6](#0-5) 

For historical events, `decrementEventRefCount` may delete events after the first pass (if their reference count reaches zero), so those segments may not be double-paid. However, the **current-segment bonus** (computed after the event loop, lines 201–213) is always re-computed from the stale checkpoint and is always double-paid.

---

### Impact Explanation

The rewards pool (`types.RewardsPoolName`) is debited twice for the current-segment bonus of the duplicated position. With `N` duplicate entries for the same position, the pool is debited `N` times. A user with a high-value, long-running position can drain the shared bonus pool significantly faster than the protocol intends, directly harming all other position holders who depend on that pool for their own bonus rewards.

The corrupted value is the `RewardsPoolName` module account balance and the `LastBonusAccrual` / `LastEventSeq` checkpoints stored in the `Positions` collection.

---

### Likelihood Explanation

Any position owner can craft a `MsgClaimTierRewards` transaction with repeated position IDs. No special privilege, leaked key, or social engineering is required. The message is a standard Cosmos SDK transaction reachable by any delegator who holds a tiered position. The only limiting factor is that the rewards pool must have sufficient balance to cover the duplicate payments; given the pool is continuously funded by the protocol, this condition is routinely satisfied.

---

### Recommendation

Add a deduplication check in `ClaimTierRewards` before building the `positions` slice, or in `msg.Validate()`. A simple seen-set over `msg.PositionIds` is sufficient:

```go
seen := make(map[uint64]struct{}, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    if _, dup := seen[posId]; dup {
        return nil, errorsmod.Wrapf(sdkerrors.ErrInvalidRequest,
            "duplicate position id %d in claim request", posId)
    }
    seen[posId] = struct{}{}
    // ... existing load + validate logic
}
```

Alternatively, reload the position state from the store inside `claimRewardsAndUpdatesPositions` rather than operating on a pre-loaded snapshot, so that the second encounter of the same ID reads the already-advanced checkpoint.

---

### Proof of Concept

1. Owner holds position ID `42` with a non-zero accrued current-segment bonus (e.g., delegated for several hours since last claim).
2. Owner submits `MsgClaimTierRewards{ Owner: owner, PositionIds: [42, 42] }`.
3. `ClaimTierRewards` loads position `42` twice into `positions[0]` and `positions[1]`, both with `LastBonusAccrual = T_prev`.
4. First loop iteration (`positions[0]`): `processEventsAndClaimBonus` computes bonus for `[T_prev, T_now]`, sends `B` coins from the rewards pool to the owner, advances `positions[0].LastBonusAccrual = T_now`, persists via `setPosition`.
5. Second loop iteration (`positions[1]`): `positions[1].LastBonusAccrual` is still `T_prev`. `processEventsAndClaimBonus` recomputes the same bonus `B` for `[T_prev, T_now]`, sends another `B` coins from the rewards pool to the owner, overwrites the persisted position with the same checkpoint.
6. Owner receives `2B` bonus tokens; the rewards pool is debited `2B` instead of `B`.
7. Repeating with `N` copies of the same ID yields `N × B` payout. [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L106-134)
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-241)
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
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L15-21)
```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
	accrualEnd := blockTime
	if pos.CompletedExitLockDuration(blockTime) {
		accrualEnd = pos.ExitUnlockAt
	}
	pos.UpdateLastBonusAccrual(accrualEnd)
}
```
