### Title
Duplicate Position IDs in `MsgClaimTierRewards` Enable Repeated Bonus Reward Draining - (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

The `ClaimTierRewards` message handler in the tiered rewards module loads all requested positions from state into a slice **before** processing any of them. If a caller includes the same position ID multiple times in `msg.PositionIds`, each duplicate entry holds a stale in-memory copy of the position (with the original `LastBonusAccrual` checkpoint). Each copy is then independently processed and paid out, allowing the same bonus reward period to be claimed N times — once per duplicate — draining the `RewardsPoolName` module account.

---

### Finding Description

`ClaimTierRewards` in `msg_server.go` first collects all positions into a slice: [1](#0-0) 

```go
positions := make([]types.PositionState, 0, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    pos, err := ms.getPositionState(ctx, posId)
    ...
    positions = append(positions, pos)
}
totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
```

All positions are read from state **before** any are processed. If `posId = X` appears twice, both slice entries carry the same pre-claim state (same `LastBonusAccrual = T0`, same `LastEventSeq = S0`).

`claimRewardsAndUpdatesPositions` then iterates the slice and for each entry calls `processEventsAndClaimBonus`: [2](#0-1) 

Inside `processEventsAndClaimBonus`, the bonus accrual window is computed from the stale `pos.LastBonusAccrual`: [3](#0-2) 

After computing and paying out bonus, the checkpoint is advanced and the position is saved: [4](#0-3) [5](#0-4) 

When the **second** duplicate entry is processed, it still holds `LastBonusAccrual = T0` (the stale pre-claim value). `processEventsAndClaimBonus` recomputes the same `[T0, blockTime]` segment and pays out the same bonus again. Even if all historical validator events have had their reference counts decremented to zero by the first pass, the "current segment" tail computation: [6](#0-5) 

still uses `segmentStart = pos.LastBonusAccrual = T0` from the stale copy, producing a non-zero bonus for the same window.

The bonus is paid directly from the module account: [7](#0-6) 

The only guard is a pool-balance check per iteration: [8](#0-7) 

This check passes as long as the pool holds enough balance for each individual payout, so an attacker can drain the entire pool by choosing N large enough that `N * single_bonus ≤ pool_balance`.

---

### Impact Explanation

The `RewardsPoolName` module account is a finite pool funded externally to pay tiered bonus APY. Repeated draining via duplicate position IDs in a single transaction extracts more bonus tokens than the position is entitled to, directly reducing the pool balance available to all other legitimate position holders. If the pool is emptied, all future `processEventsAndClaimBonus` calls revert with `ErrInsufficientBonusPool`, freezing bonus reward claims for every user.

---

### Likelihood Explanation

Any position owner can craft a `MsgClaimTierRewards` transaction with a repeated position ID list. No special privilege, validator access, or timing constraint is required beyond holding a valid tiered position with accrued bonus. The transaction is a standard user-facing message reachable via CLI or gRPC. The attack is profitable whenever the bonus pool holds more than one period's worth of rewards for the attacker's position.

---

### Recommendation

Deduplicate `msg.PositionIds` before loading positions, either in `MsgClaimTierRewards.Validate()` or at the start of the handler:

```go
seen := make(map[uint64]struct{}, len(msg.PositionIds))
for _, id := range msg.PositionIds {
    if _, dup := seen[id]; dup {
        return nil, errorsmod.Wrapf(sdkerrors.ErrInvalidRequest, "duplicate position id %d", id)
    }
    seen[id] = struct{}{}
}
```

Alternatively, re-load each position from state immediately before processing it inside `claimRewardsAndUpdatesPositions` rather than operating on a pre-loaded slice, so that the updated checkpoint from the first pass is visible to any subsequent pass.

---

### Proof of Concept

1. User creates a tiered position (position ID = 42) and waits for bonus rewards to accrue.
2. User submits `MsgClaimTierRewards{ Owner: user, PositionIds: [42, 42, 42, ..., 42] }` with N repetitions.
3. All N copies of position 42 are loaded with `LastBonusAccrual = T0` before any processing begins.
4. Each iteration of `claimRewardsAndUpdatesPositions` computes bonus for `[T0, blockTime]` and transfers it from `RewardsPoolName` to the user.
5. User receives `N × single_period_bonus` instead of `1 × single_period_bonus`, draining the pool by `(N-1) × single_period_bonus`.

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L164-166)
```go
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual

```

**File:** x/tieredrewards/keeper/claim_rewards.go (L206-213)
```go
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-217)
```go
	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-233)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}

```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-239)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
```
