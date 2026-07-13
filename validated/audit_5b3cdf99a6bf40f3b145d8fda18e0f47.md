### Title
Duplicate Position IDs in `MsgClaimTierRewards` Enable Double-Claiming Bonus Rewards - (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`ClaimTierRewards` in the tieredrewards module accepts a caller-supplied list of position IDs and processes each one sequentially without deduplication. When the same position ID appears twice in `msg.PositionIds`, the bonus reward computation runs twice against the same stale in-memory snapshot, draining the `RewardsPoolName` module account beyond what the position is entitled to.

---

### Finding Description

`ClaimTierRewards` builds a `positions` slice by iterating over the caller-supplied `msg.PositionIds`: [1](#0-0) 

All positions are loaded from the KV store **before** any rewards are processed. If the same ID appears twice, both entries in the slice carry the identical in-memory `PositionState` — including the same `LastEventSeq` checkpoint.

The slice is then handed to `claimRewardsAndUpdatesPositions`: [2](#0-1) 

For each entry it calls `processEventsAndClaimBonus`, which fetches validator events **from the KV store** using the in-memory `pos.LastEventSeq`: [3](#0-2) 

On the **first** iteration for position X:
- Events since `LastEventSeq = S` are fetched, bonus is computed and paid, each event's `ReferenceCount` is decremented, and the updated position (with new `LastEventSeq`) is written back to the KV store via `setPosition`.

On the **second** iteration for the duplicate copy of position X:
- The in-memory copy still holds `LastEventSeq = S` (loaded before the first iteration ran).
- `getValidatorEventsSince(ctx, valAddr, S)` is called again. Because `ReferenceCount` was set to the **total number of positions on the validator** at event-creation time: [4](#0-3) 

  if that count was > 1, the events still exist after the first decrement. The same events are replayed, the same bonus is computed, and `bankKeeper.SendCoinsFromModuleToAccount` transfers bonus coins a second time from the rewards pool: [5](#0-4) 

Base rewards are **not** doubled because `distributionKeeper.WithdrawDelegationRewards` returns zero on the second call (already withdrawn). Only bonus rewards from the module pool are duplicated.

---

### Impact Explanation

The `RewardsPoolName` module account is drained at a rate proportional to the number of duplicate IDs submitted and the accrued bonus. An attacker with a position on any validator that has at least two positions (a common condition) can multiply their bonus payout by N by submitting the same position ID N times in a single transaction, subject only to the pool balance check: [6](#0-5) 

The corrupted value is the `RewardsPoolName` module account balance and the per-position `LastEventSeq` accounting invariant (event reference counts are decremented more times than positions actually consumed them).

---

### Likelihood Explanation

The entry path is a standard, unprivileged `MsgClaimTierRewards` transaction signed by any position owner. No governance, admin key, or social engineering is required. The only precondition — that the target validator has more than one tiered-rewards position — is the normal operating state of any active validator on the network.

---

### Recommendation

**Short term:** Add a duplicate-ID check in `ClaimTierRewards` before building the `positions` slice:

```go
seen := make(map[uint64]struct{}, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    if _, dup := seen[posId]; dup {
        return nil, errorsmod.Wrapf(types.ErrInvalidPositionID, "duplicate position id %d", posId)
    }
    seen[posId] = struct{}{}
    // ... existing load logic
}
```

**Long term:** Enforce uniqueness in `MsgClaimTierRewards.Validate()` so the check is applied at the message-validation layer before the message reaches the keeper. [7](#0-6) 

---

### Proof of Concept

1. Attacker owns position ID `42`, delegated to validator `V` which has 3 tiered-rewards positions total.
2. Validator `V` transitions (e.g., unbond/rebond), creating a `ValidatorEvent` with `ReferenceCount = 3`.
3. Attacker submits `MsgClaimTierRewards { owner: attacker, position_ids: [42, 42, 42] }`.
4. The keeper loads position 42 three times (all with `LastEventSeq = S`).
5. Iteration 1: event processed, bonus paid, `ReferenceCount` decremented to 2, position saved with `LastEventSeq = S+1`.
6. Iteration 2: in-memory copy still has `LastEventSeq = S`; event still exists (`ReferenceCount = 2 > 0`); bonus paid again, `ReferenceCount` decremented to 1.
7. Iteration 3: same; `ReferenceCount = 1 > 0`; bonus paid a third time, event deleted.
8. Attacker receives 3× the entitled bonus; the other two legitimate positions on validator `V` will find their event already deleted and receive zero bonus when they later claim. [8](#0-7)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L153-156)
```go
	events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-199)
```go
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-241)
```go
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
