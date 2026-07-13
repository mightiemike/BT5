### Title
Duplicate Position IDs in `MsgClaimTierRewards` Allow Double-Claiming Bonus Rewards - (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

The `ClaimTierRewards` message handler pre-loads all positions into a slice before any state updates occur and performs no deduplication on the caller-supplied `PositionIds` list. Submitting the same position ID twice causes `processEventsAndClaimBonus` to execute twice against the same stale in-memory state, paying out the current-segment bonus twice from the shared `RewardsPoolName` module account.

---

### Finding Description

In `ClaimTierRewards`, all positions are fetched from chain state and appended to a local slice before any processing begins: [1](#0-0) 

If `msg.PositionIds` contains the same ID twice (e.g., `[42, 42]`), both entries in `positions` are loaded from the same on-chain snapshot and hold identical `LastBonusAccrual`, `LastEventSeq`, and `LastKnownBonded` values.

`claimRewardsAndUpdatesPositions` then iterates over the slice independently: [2](#0-1) 

For each entry it calls `processEventsAndClaimBonus`, which computes the **current-segment bonus** using `pos.LastBonusAccrual` as the segment start and the current block time as the end: [3](#0-2) [4](#0-3) 

After the first iteration, `applyBonusAccrualCheckpoint` advances `pos.LastBonusAccrual` to `blockTime` **in the first copy only**: [5](#0-4) 

`setPosition` then persists the updated first copy to chain state. However, the **second copy** (`positions[1]`) still holds the original stale `LastBonusAccrual`. When the loop reaches it, `processEventsAndClaimBonus` recomputes the identical segment `[LastBonusAccrual, blockTime]` and issues a second `SendCoinsFromModuleToAccount` transfer from the bonus pool: [6](#0-5) 

The same double-execution applies to historical validator events: `getValidatorEventsSince` is called with the stale `pos.LastEventSeq` from the second copy, so any events that were not fully pruned (i.e., their reference count was not reduced to zero by the first iteration because other positions share the same validator) are replayed and their bonus segments are paid again.

---

### Impact Explanation

An attacker who owns any tiered-rewards position can drain the `RewardsPoolName` module account at an N× multiplier by including their position ID N times in a single `MsgClaimTierRewards` transaction. The corrupted value is the bonus pool balance and the attacker's token balance. Additionally, reference counts for shared validator events are decremented N times, which can cause other legitimate position holders on the same validator to lose their pending bonus rewards (their events are pruned before they can claim).

---

### Likelihood Explanation

The entry path is a standard, unprivileged Cosmos SDK transaction (`MsgClaimTierRewards`) available to any position owner. No special role, leaked key, or social engineering is required. The only prerequisite is holding at least one tiered-rewards position with accrued bonus, which is the normal operating state for any participant in the module.

---

### Recommendation

Add explicit deduplication of `PositionIds` either in `MsgClaimTierRewards.Validate()` (in `x/tieredrewards/types/msgs.go`) or at the top of the `ClaimTierRewards` handler before the position-loading loop. A simple approach is to build a `map[uint64]struct{}` over the IDs and return an error if any duplicate is detected.

---

### Proof of Concept

1. Alice holds position ID `42` on a bonded validator with `LastBonusAccrual = T0` and accrued bonus `B`.
2. Alice broadcasts `MsgClaimTierRewards{Owner: alice, PositionIds: [42, 42]}`.
3. The handler loads `positions = [pos42_snapshot, pos42_snapshot]` — both with `LastBonusAccrual = T0`.
4. **Iteration 0**: `processEventsAndClaimBonus` computes segment `[T0, blockTime]`, sends `B` tokens to Alice, advances `positions[0].LastBonusAccrual` to `blockTime`, persists via `setPosition`.
5. **Iteration 1**: `positions[1].LastBonusAccrual` is still `T0`; `processEventsAndClaimBonus` recomputes the same segment `[T0, blockTime]`, sends another `B` tokens to Alice.
6. Alice receives `2B` instead of `B`; the bonus pool is short by `B`.

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L429-451)
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L164-166)
```go
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual

```

**File:** x/tieredrewards/keeper/claim_rewards.go (L206-212)
```go
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-217)
```go
	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-241)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}
```
