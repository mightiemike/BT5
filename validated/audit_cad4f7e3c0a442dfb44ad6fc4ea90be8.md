### Title
Duplicate Position IDs in `MsgClaimTierRewards` Enable Double-Claiming of Bonus Rewards from Shared Pool — (File: x/tieredrewards/keeper/msg_server.go)

---

### Summary

`ClaimTierRewards` loads all requested positions into a slice **before** processing any of them. If a caller includes the same position ID more than once in `msg.PositionIds`, the stale in-memory copy retains the original `LastBonusAccrual` checkpoint, causing `processEventsAndClaimBonus` to recompute and pay the current-segment bonus a second time from the shared `RewardsPoolName` module account.

---

### Finding Description

In `ClaimTierRewards`, all positions are fetched from state and appended to a local slice before any reward processing begins: [1](#0-0) 

The slice is then handed to `claimRewardsAndUpdatesPositions`, which iterates over it sequentially: [2](#0-1) 

If `msg.PositionIds` contains `[N, N]`, two copies of position `N` are loaded with identical `LastBonusAccrual` and `LastEventSeq` values. The first copy is processed correctly: `processEventsAndClaimBonus` walks events, computes the current-segment bonus, pays it from the pool, advances `LastBonusAccrual` to `blockTime`, and persists the updated position via `setPosition`.

The second copy, however, still holds the **original** `LastBonusAccrual = T0`. When `processEventsAndClaimBonus` runs on it, the current-segment bonus calculation uses `segmentStart = T0` again: [3](#0-2) 

This recomputes the identical bonus for `[T0, blockTime]` and issues a second `SendCoinsFromModuleToAccount` from `RewardsPoolName`: [4](#0-3) 

The `decrementEventRefCount` mechanism only protects against replaying already-consumed historical events; it does **not** protect the current-segment bonus, which is computed directly from live validator state and the stale `LastBonusAccrual` field.

The final `setPosition` call for the second copy overwrites the correctly-checkpointed state with an identically-checkpointed state (both end at `blockTime`), so the on-chain position looks correct after the transaction — but the pool has been debited twice. [5](#0-4) 

---

### Impact Explanation

The `RewardsPoolName` module account is a shared pool that funds bonus rewards for **all** position holders. An attacker who owns a single position can drain it by submitting `MsgClaimTierRewards` with `PositionIds = [id, id, id, …]` (N copies), receiving N times the legitimate bonus in one transaction. Once the pool is exhausted, every other position holder's call to claim bonus rewards fails with `ErrInsufficientBonusPool`, permanently denying them their earned rewards. [6](#0-5) 

---

### Likelihood Explanation

Any account that has locked funds into a tier and holds a valid position can trigger this. No governance role, validator key, or privileged access is required. The attacker only needs to craft a standard `MsgClaimTierRewards` transaction with a repeated position ID — a trivially constructed Cosmos SDK message. The attack is profitable as long as the bonus pool has a non-zero balance and the position has accrued any time since its last `LastBonusAccrual` checkpoint.

---

### Recommendation

1. **Deduplicate in `Validate()`**: Add a check in `MsgClaimTierRewards.Validate()` (or `msg_validate.go`) that rejects messages where `PositionIds` contains duplicate entries.
2. **Alternatively, deduplicate at the handler level**: Before appending to the `positions` slice in `ClaimTierRewards`, maintain a `seen` map of position IDs and skip or error on duplicates. [7](#0-6) 

---

### Proof of Concept

```
1. Attacker creates a tier position (ID = 7) and waits for bonus to accrue.
   LastBonusAccrual = T0, accrued bonus for [T0, T1] = B tokens.

2. Attacker broadcasts MsgClaimTierRewards{Owner: attacker, PositionIds: [7, 7, 7]}.

3. Handler loads positions slice: [{pos7, LastBonusAccrual=T0}, {pos7, LastBonusAccrual=T0}, {pos7, LastBonusAccrual=T0}]

4. Iteration i=0:
   - processEventsAndClaimBonus: segmentStart=T0, segmentEnd=T1 → bonus=B
   - SendCoinsFromModuleToAccount(RewardsPoolName → attacker, B)
   - setPosition: LastBonusAccrual=T1 saved to state

5. Iteration i=1 (stale copy, LastBonusAccrual still T0):
   - processEventsAndClaimBonus: segmentStart=T0, segmentEnd=T1 → bonus=B again
   - SendCoinsFromModuleToAccount(RewardsPoolName → attacker, B)  ← second payment
   - setPosition: LastBonusAccrual=T1 saved (same value, no visible anomaly)

6. Iteration i=2: same as i=1, third payment of B.

Result: attacker receives 3B; pool debited 3B; on-chain position state appears normal.
Other position holders subsequently fail with ErrInsufficientBonusPool.
``` [8](#0-7)

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

**File:** x/tieredrewards/keeper/bonus_rewards.go (L48-61)
```go
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
	if bonus.IsZero() {
		return nil
	}

	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetAllBalances(ctx, poolAddr)
	if !poolBalance.IsAllGTE(bonus) {
		return errorsmod.Wrapf(types.ErrInsufficientBonusPool,
			"bonus: %s, pool balance: %s",
			bonus.String(), poolBalance.String())
	}

	return nil
```
