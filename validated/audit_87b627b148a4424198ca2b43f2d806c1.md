### Title
Duplicate Position IDs in `MsgClaimTierRewards` Allow Double-Claiming Bonus Rewards - (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

The `ClaimTierRewards` message handler loads all requested positions into a slice **before** processing any of them. Because there is no deduplication check on `msg.PositionIds`, an attacker who owns a position can include the same position ID multiple times in a single `MsgClaimTierRewards` transaction. Each duplicate entry carries the same pre-update checkpoint state, causing `processEventsAndClaimBonus` to compute and pay the current-segment bonus reward multiple times, draining the `RewardsPoolName` module account.

---

### Finding Description

In `ClaimTierRewards`, all positions are fetched from chain state and appended to a local slice before any reward processing begins: [1](#0-0) 

```go
positions := make([]types.PositionState, 0, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    pos, err := ms.getPositionState(ctx, posId)
    ...
    if err := ms.validateClaimRewards(pos.Position, msg.Owner); err != nil {
        return nil, err
    }
    positions = append(positions, pos)
}
totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
```

`validateClaimRewards` only checks ownership — it does not detect duplicate IDs. If `msg.PositionIds = [42, 42]`, the same position state (with the same `LastBonusAccrual` and `LastEventSeq` checkpoints) is appended twice.

`claimRewardsAndUpdatesPositions` then iterates over the slice and processes each entry independently: [2](#0-1) 

Inside `processEventsAndClaimBonus`, the current-segment bonus is computed using `pos.LastBonusAccrual` as the segment start and the current `blockTime` as the end: [3](#0-2) 

```go
segmentStart := pos.LastBonusAccrual
...
if bonded && val.IsBonded() {
    currentRate, err := k.getTokensPerShare(ctx, valAddr)
    bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
    totalBonus = totalBonus.Add(bonus)
}
applyBonusAccrualCheckpoint(&pos.Position, blockTime)
```

On the **first** iteration for position 42: the segment `[LastBonusAccrual, blockTime]` is computed, the bonus is sent via `bankKeeper.SendCoinsFromModuleToAccount`, and `setPosition` writes the updated checkpoint to chain state.

On the **second** iteration for position 42: the in-memory entry in `positions[1]` still holds the **original** `LastBonusAccrual` (loaded before the first iteration ran). `processEventsAndClaimBonus` therefore recomputes the identical segment `[original_LastBonusAccrual, blockTime]` and calls `bankKeeper.SendCoinsFromModuleToAccount` a second time, paying the same bonus again from `RewardsPoolName`. [4](#0-3) 

The base-reward path (`WithdrawDelegationRewards`) is idempotent and returns zero on the second call, so only bonus rewards are double-paid. The only guard is `sufficientBonusPoolBalance`, which passes as long as the pool holds enough funds — a normal operating condition.

---

### Impact Explanation

An attacker who owns any tiered-rewards position with a non-zero accrued bonus can drain the `RewardsPoolName` module account. By including the same position ID `N` times in `msg.PositionIds`, they receive `N × (current-segment bonus)` in a single transaction. The corrupted value is the `RewardsPoolName` module account balance (bonus pool), which is a shared resource funding all participants' bonus rewards.

---

### Likelihood Explanation

The attack requires no privileged role. Any delegator who has created a tiered-rewards position and waited for bonus to accrue can submit a standard `MsgClaimTierRewards` transaction with a repeated position ID. The entry path is a normal, unprivileged Cosmos SDK transaction. The only precondition is that the bonus pool holds sufficient balance, which is the expected steady-state.

---

### Recommendation

Add a deduplication check on `msg.PositionIds` either in `MsgClaimTierRewards.Validate()` or at the start of the `ClaimTierRewards` handler before the position-loading loop:

```go
seen := make(map[uint64]struct{}, len(msg.PositionIds))
for _, id := range msg.PositionIds {
    if _, dup := seen[id]; dup {
        return nil, errorsmod.Wrapf(types.ErrDuplicatePositionID, "position id %d appears more than once", id)
    }
    seen[id] = struct{}{}
}
```

Alternatively, enforce uniqueness in the protobuf message validation layer so malformed messages are rejected before reaching the keeper.

---

### Proof of Concept

```go
func TestClaimTierRewards_DuplicatePositionID(t *testing.T) {
    // Setup: owner creates a position and waits for bonus to accrue
    // positionID = 42, owner = attacker

    // Advance time so LastBonusAccrual is in the past
    ctx = ctx.WithBlockTime(ctx.BlockTime().Add(24 * time.Hour))

    // Submit ClaimTierRewards with the same position ID twice
    msg := &types.MsgClaimTierRewards{
        Owner:       attacker.String(),
        PositionIds: []uint64{42, 42},  // duplicate
    }
    resp, err := msgServer.ClaimTierRewards(ctx, msg)
    require.NoError(t, err)

    // BonusRewards should equal 2× the single-position bonus
    singleBonus := computeExpectedBonus(position, tier, originalLastBonusAccrual, blockTime)
    require.Equal(t, singleBonus.MulRaw(2), resp.BonusRewards.AmountOf(bondDenom))

    // RewardsPool was debited twice
    poolBalance := bankKeeper.GetBalance(ctx, rewardsPoolAddr, bondDenom)
    require.Equal(t, initialPoolBalance.Sub(singleBonus.MulRaw(2)), poolBalance)
}
```

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L434-446)
```go
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L164-215)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-241)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}
```
