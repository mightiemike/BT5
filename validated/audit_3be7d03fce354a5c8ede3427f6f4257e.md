### Title
Duplicate Position IDs in `MsgClaimTierRewards` Enable Multiple Bonus Reward Drains — (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`ClaimTierRewards` loads all requested positions into a local slice before processing, with no deduplication of `msg.PositionIds`. Submitting the same position ID N times in one transaction causes `processEventsAndClaimBonus` to re-process the same validator events N times and transfer bonus rewards from the `RewardsPoolName` module account N times, draining the pool.

---

### Finding Description

In `ClaimTierRewards`, all positions are loaded from the store into a `[]types.PositionState` slice **before** any processing begins: [1](#0-0) 

Each entry in `positions` carries its own in-memory copy of `LastEventSeq` as it existed at load time. The slice is then handed to `claimRewardsAndUpdatesPositions`: [2](#0-1) 

Inside `claimRewardsAndUpdatesPositions`, each element is processed independently: [3](#0-2) 

`processEventsAndClaimBonus` reads `pos.LastEventSeq` from the **in-memory struct**, not from the store: [4](#0-3) 

After the first iteration for a duplicated position ID, `setPosition` persists the updated `LastEventSeq` to the store. However, the second copy in the slice still holds the **original** `LastEventSeq`, so `getValidatorEventsSince` returns the same events again, and bonus rewards are paid a second time: [5](#0-4) 

A secondary corruption also occurs: `decrementEventRefCount` is called once per event per iteration, so duplicate processing decrements each event's reference count twice, potentially triggering premature event deletion and corrupting the event log for other positions sharing the same validator: [6](#0-5) 

---

### Impact Explanation

An attacker who owns any position with non-zero accrued bonus rewards can drain the entire `RewardsPoolName` module account in a single transaction by repeating their position ID enough times. The only practical ceiling is the transaction gas limit and the pool balance. The corrupted value is the `RewardsPoolName` module account balance (staking bond denom), which is a shared pool funding bonus rewards for all participants.

---

### Likelihood Explanation

The entry path is a standard, unprivileged `MsgClaimTierRewards` transaction. Any position owner can craft it. No special role, key, or governance action is required. The only prerequisite is holding a position with some accumulated bonus — a normal operational state.

---

### Recommendation

Add an explicit duplicate-ID check in `MsgClaimTierRewards.Validate()` (in `x/tieredrewards/types/msgs.go`) or at the top of the `ClaimTierRewards` handler before the load loop. Reject the transaction if any position ID appears more than once in `msg.PositionIds`.

---

### Proof of Concept

1. Call `MsgLockTier` to create a position; record `posId`.
2. Wait for the validator to accumulate bonded time so `processEventsAndClaimBonus` would pay a non-zero bonus.
3. Broadcast a single `MsgClaimTierRewards` with `PositionIds: [posId, posId, posId, …]` repeated N times.
4. `ClaimTierRewards` loads N identical copies of the position (all with the same `LastEventSeq`).
5. `claimRewardsAndUpdatesPositions` iterates N times; each iteration calls `processEventsAndClaimBonus` with the pre-load `LastEventSeq`, re-processes the same events, and executes `SendCoinsFromModuleToAccount` N times.
6. The caller receives N × (single-claim bonus) tokens; the `RewardsPoolName` account is drained by that amount. [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L234-240)
```go
	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
```
