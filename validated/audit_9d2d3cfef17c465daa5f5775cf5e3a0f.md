### Title
Duplicate Position IDs in `MsgClaimTierRewards` Enable Double-Claim of Bonus Rewards - (File: `x/tieredrewards/keeper/msg_server.go`)

### Summary
`ClaimTierRewards` loads position states into a slice from caller-supplied `PositionIds` without deduplicating them. When the same position ID appears twice, `claimRewardsAndUpdatesPositions` processes two independent in-memory copies of the same position, each paying out the full bonus reward. The rewards pool is drained by twice the legitimate amount per duplicated entry.

### Finding Description

In `ClaimTierRewards`, the message handler iterates over the caller-supplied `msg.PositionIds` slice and appends each loaded `PositionState` to a local `positions` slice with no uniqueness check: [1](#0-0) 

If `msg.PositionIds = [5, 5]`, two independent copies of position 5 are appended — both carrying the same initial `LastBonusAccrual` timestamp `T0` and `LastEventSeq` value `S0`.

`claimRewardsAndUpdatesPositions` then iterates over the slice by index, taking a pointer to each element: [2](#0-1) 

`processEventsAndClaimBonus` is called on each copy independently. It computes the bonus for the segment `[LastBonusAccrual, blockTime]` and calls `bankKeeper.SendCoinsFromModuleToAccount` to transfer bonus coins from the `RewardsPoolName` module account to the owner: [3](#0-2) 

Because `positions[0]` and `positions[1]` are separate in-memory structs loaded from the same store key before the loop, the second iteration starts with the original `LastBonusAccrual = T0` and `LastEventSeq = S0`. It recomputes the identical bonus `B` and issues a second `SendCoinsFromModuleToAccount` call. The final `setPosition` call in each iteration persists the same advanced checkpoint, so the on-chain position state ends up correct — but the owner has received `2B` instead of `B`.

The cleanest exploit path requires **no pending validator events**: when the validator is currently bonded and no events exist since the last claim, `processEventsAndClaimBonus` skips the event loop entirely (no `decrementEventRefCount` calls that could surface an error) and goes directly to the current-segment bonus computation: [4](#0-3) 

Both iterations execute this branch identically, each paying `B` to the owner. No intermediate state change prevents the second payment.

### Impact Explanation

The `RewardsPoolName` module account balance is drained at twice the legitimate rate per duplicated position ID. An attacker who owns a position with accumulated bonus rewards can multiply their payout by including the same position ID `N` times in a single `MsgClaimTierRewards` transaction, subject only to the pool having sufficient balance (`N × B`). This constitutes unauthorized extraction of funds from the shared rewards pool, directly harming all other participants whose future bonus rewards depend on that pool.

### Likelihood Explanation

The entry path is a standard, unprivileged Cosmos SDK transaction (`MsgClaimTierRewards`) signed by any delegator who holds at least one tiered-rewards position. No special role, leaked key, or social engineering is required. The exploit is deterministic and repeatable every block. Any user who discovers the missing deduplication check can drain the pool incrementally.

### Recommendation

Add a duplicate-ID check before appending to the `positions` slice in `ClaimTierRewards`. A simple approach is to maintain a `seen` map:

```go
seen := make(map[uint64]struct{}, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    if _, ok := seen[posId]; ok {
        return nil, errorsmod.Wrapf(types.ErrInvalidPositionID, "duplicate position id %d", posId)
    }
    seen[posId] = struct{}{}
    // ... existing load and validate logic
}
```

Alternatively, enforce uniqueness in `MsgClaimTierRewards.Validate()` so the check occurs at the message-validation layer before any keeper logic runs.

### Proof of Concept

1. Alice owns position ID `5`, delegated to a bonded validator, with `LastBonusAccrual = T0`. Accrued bonus since `T0` is `B`.
2. Alice submits `MsgClaimTierRewards{Owner: alice, PositionIds: [5, 5]}`.
3. `ClaimTierRewards` loads position 5 twice → `positions = [pos5_copy1, pos5_copy2]`, both with `LastBonusAccrual = T0`.
4. First loop iteration: `processEventsAndClaimBonus(pos5_copy1)` computes bonus `B`, calls `SendCoinsFromModuleToAccount(RewardsPoolName, alice, B)`, advances `pos5_copy1.LastBonusAccrual` to `blockTime`.
5. Second loop iteration: `processEventsAndClaimBonus(pos5_copy2)` starts from `LastBonusAccrual = T0` (original value), computes the same bonus `B`, calls `SendCoinsFromModuleToAccount(RewardsPoolName, alice, B)` again.
6. Alice receives `2B`. The rewards pool loses `2B` instead of `B`. The persisted position checkpoint is identical to a legitimate single claim. [5](#0-4) [2](#0-1) [6](#0-5)

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

**File:** x/tieredrewards/types/keys.go (L6-18)
```go
	ParamsKey                         = collections.NewPrefix(0)
	TiersKey                          = collections.NewPrefix(1)
	PositionsKey                      = collections.NewPrefix(2)
	NextPositionIdKey                 = collections.NewPrefix(3)
	PositionsByOwnerKey               = collections.NewPrefix(4)
	PositionsByTierKey                = collections.NewPrefix(5)
	PositionCountByTierKey            = collections.NewPrefix(6)
	ValidatorEventsKey                = collections.NewPrefix(7)
	ValidatorEventSeqKey              = collections.NewPrefix(8)
	PositionCountByValidatorKey       = collections.NewPrefix(9)
	RedelegationMappingsKey           = collections.NewPrefix(10)
	RedelegationMappingsByPositionKey = collections.NewPrefix(11)
)
```
