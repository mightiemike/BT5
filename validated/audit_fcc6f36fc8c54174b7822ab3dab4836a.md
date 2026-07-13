### Title
Duplicate Position ID in `MsgClaimTierRewards` Enables Double-Claim of Bonus Rewards — (`File: x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`ClaimTierRewards` accepts a caller-supplied slice of position IDs and loads all positions from the store **before** any processing begins. Because there is no deduplication guard on the input slice, submitting the same position ID twice causes the same position's bonus rewards to be paid out twice from the `RewardsPoolName` module account.

---

### Finding Description

In `ClaimTierRewards` (msg_server.go lines 429–468), the handler first loads every requested position into an in-memory slice, then passes the entire slice to `claimRewardsAndUpdatesPositions`:

```go
// msg_server.go:434-446
positions := make([]types.PositionState, 0, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    pos, err := ms.getPositionState(ctx, posId)   // reads from store
    ...
    positions = append(positions, pos)
}
totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
``` [1](#0-0) 

If `msg.PositionIds = [X, X]`, both `positions[0]` and `positions[1]` are loaded with the **same pre-claim state** from the store.

Inside `claimRewardsAndUpdatesPositions`, each position is processed sequentially:

```go
// claim_rewards.go:110-134
for i := range positions {
    pos := &positions[i]
    base, err := k.claimBaseRewards(ctx, *pos)
    ...
    bonus, err := k.processEventsAndClaimBonus(ctx, pos)
    ...
    if err := k.setPosition(ctx, pos.Position, nil); err != nil { ... }
}
``` [2](#0-1) 

After iteration 1 completes for `positions[0]`:
- The store is updated with the new `LastEventSeq` / `LastBonusAccrual` checkpoints.
- Validator event reference counts are decremented.

Iteration 2 then processes `positions[1]`, which still holds the **original (pre-claim) `LastEventSeq`**. `processEventsAndClaimBonus` calls `getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)` using this stale sequence number:

```go
// claim_rewards.go:153-156
events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
``` [3](#0-2) 

If the validator events are still present in the store (their reference count is > 0 because other positions are also delegated to the same validator), `processEventsAndClaimBonus` recomputes the same bonus amount and executes a second `SendCoinsFromModuleToAccount` transfer:

```go
// claim_rewards.go:239
if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
``` [4](#0-3) 

The base rewards (`WithdrawDelegationRewards`) are safe because the distribution module resets the outstanding reward to zero after the first withdrawal. The bonus rewards are **not** safe because the guard is the position's `LastEventSeq` checkpoint, which is only persisted to the store at the end of each iteration — too late to protect the second copy.

---

### Impact Explanation

An attacker who owns a tiered position can drain the `RewardsPoolName` module account by submitting `MsgClaimTierRewards` with the same position ID repeated N times. Each repetition beyond the first extracts an additional full bonus payout for that position. The corrupted value is the `RewardsPoolName` module account balance (bonus pool), which is a shared pool funding all tiered-reward participants.

---

### Likelihood Explanation

The entry path is a standard, unprivileged `MsgClaimTierRewards` transaction. The only precondition is that at least one other tiered position exists on the same validator (so the validator event reference count remains > 0 after the first iteration's decrements). The attacker can trivially satisfy this by creating a second minimal position on the same validator before executing the attack. No privileged access, leaked keys, or social engineering is required.

---

### Recommendation

1. **Deduplicate `msg.PositionIds` in `MsgClaimTierRewards.Validate()`** — reject the message if any position ID appears more than once.
2. **Re-read position state from the store inside the processing loop** rather than using a pre-loaded in-memory snapshot, so that the updated `LastEventSeq` is always visible to subsequent iterations.
3. As a defense-in-depth measure, advance and persist the `LastEventSeq` checkpoint **before** transferring bonus coins (checks-effects-interactions pattern).

---

### Proof of Concept

1. Attacker creates position `A` delegated to validator `V` (minimum lock amount).
2. Attacker (or any other user) creates a second position `B` also delegated to `V`, ensuring the validator event reference count is ≥ 2.
3. Validator `V` accrues bonus events (bond/unbond/slash) so that position `A` has a non-zero pending bonus.
4. Attacker submits `MsgClaimTierRewards { owner: attacker, position_ids: [A, A] }`.
5. Iteration 1: loads `posA` (original state), claims bonus `B_A`, decrements event ref counts (count goes from 2 → 1, events remain), persists updated `posA` to store.
6. Iteration 2: uses the pre-loaded stale `posA` (original `LastEventSeq`), `getValidatorEventsSince` returns the same events (ref count = 1 > 0), recomputes bonus `B_A`, calls `SendCoinsFromModuleToAccount` a second time — attacker receives `2 × B_A` total.
7. The `RewardsPoolName` balance is reduced by `2 × B_A` instead of `1 × B_A`. [5](#0-4) [6](#0-5) [7](#0-6)

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
