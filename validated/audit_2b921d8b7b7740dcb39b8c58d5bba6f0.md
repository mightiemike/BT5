### Title
Duplicate `positionId` in `MsgClaimTierRewards.PositionIds` enables N-fold bonus drain from `RewardsPoolName` — (`x/tieredrewards/keeper/claim_rewards.go` / `msg_server.go`)

### Summary

`ClaimTierRewards` pre-loads all position snapshots from on-chain state into a slice **before** any write-back occurs. If the same `positionId` appears N times in `msg.PositionIds`, N identical stale snapshots are loaded. `claimRewardsAndUpdatesPositions` then processes each snapshot independently, paying out the full bonus for each copy. No deduplication guard exists anywhere in the validation path.

---

### Finding Description

**Entrypoint:** `msgServer.ClaimTierRewards` — `x/tieredrewards/keeper/msg_server.go`

**Phase 1 — snapshot loading (lines 434–446):**

```go
positions := make([]types.PositionState, 0, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    pos, err := ms.getPositionState(ctx, posId)   // reads live store
    ...
    positions = append(positions, pos)
}
```

If `msg.PositionIds = [posId, posId, posId]`, `getPositionState` is called three times for the same key. Because no write-back has happened yet, all three calls return the **same stale snapshot**. The `positions` slice now holds N identical copies. [1](#0-0) 

**Phase 2 — processing (lines 110–134 of `claim_rewards.go`):**

```go
for i := range positions {
    pos := &positions[i]
    ...
    bonus, err := k.processEventsAndClaimBonus(ctx, pos)
    ...
    if err := k.setPosition(ctx, pos.Position, nil); err != nil { ... }
}
```

Each iteration operates on its own pre-loaded snapshot. `setPosition` writes back the updated state after each iteration, but the **next iteration's snapshot was already loaded with the old `LastBonusAccrual` and `LastEventSeq`**. [2](#0-1) 

**Phase 3 — bonus computation in `processEventsAndClaimBonus`:**

```go
segmentStart := pos.LastBonusAccrual   // OLD value from stale snapshot
...
// current-segment bonus
if bonded && val.IsBonded() {
    bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
    totalBonus = totalBonus.Add(bonus)
}
applyBonusAccrualCheckpoint(&pos.Position, blockTime)  // updates in-memory only
```

For iteration 2, `segmentStart` is still the old `LastBonusAccrual`. The current-segment bonus (from `LastBonusAccrual` → `blockTime`) is recomputed identically and paid again via `SendCoinsFromModuleToAccount`. [3](#0-2) 

**Event-based segments:** `decrementEventRefCount` may delete events after iteration 1 (when `ReferenceCount ≤ 1`), so event-loop segments may not repeat. However, the **current-segment bonus is always recomputed** from the stale `LastBonusAccrual`, making it the guaranteed drain vector regardless of event deletion. [4](#0-3) 

**No deduplication guard:** `validateClaimRewards` only checks ownership:

```go
func (k Keeper) validateClaimRewards(pos types.Position, owner string) error {
    if !pos.IsOwner(owner) {
        return types.ErrNotPositionOwner
    }
    return nil
}
``` [5](#0-4) 

There is no check in `msg.Validate()` or anywhere else that rejects or deduplicates repeated position IDs.

---

### Impact Explanation

An unprivileged position owner submits one `MsgClaimTierRewards` with `PositionIds = [posId, posId, ..., posId]` (N repetitions). The `RewardsPoolName` module account is drained by N × (single-position bonus), while the owner receives N × bonus. This is a direct, unbacked transfer of funds from the module account to the attacker — a critical accounting invariant violation. [6](#0-5) [7](#0-6) 

---

### Likelihood Explanation

The attack requires only:
1. Owning any position with accrued bonus (normal user activity).
2. Constructing a `MsgClaimTierRewards` with the same `positionId` repeated N times — trivially done via CLI or direct protobuf construction.
3. The `RewardsPoolName` pool having sufficient balance (it is funded by the protocol).

No governance, privileged role, or special configuration is needed.

---

### Recommendation

Add a deduplication check in `ClaimTierRewards` before loading positions, or in `MsgClaimTierRewards.Validate()`:

```go
// In MsgClaimTierRewards.Validate() or at the top of ClaimTierRewards:
seen := make(map[uint64]struct{}, len(msg.PositionIds))
for _, id := range msg.PositionIds {
    if _, dup := seen[id]; dup {
        return nil, errorsmod.Wrapf(types.ErrInvalidPositionID, "duplicate position id %d", id)
    }
    seen[id] = struct{}{}
}
```

Alternatively, enforce uniqueness at the `ValidateBasic`/`Validate` level on the message type so it is rejected before reaching the keeper.

---

### Proof of Concept

1. Create one position with accrued bonus (e.g., wait one block after delegation to a bonded validator).
2. Record `RewardsPoolName` balance = B.
3. Submit `MsgClaimTierRewards{Owner: owner, PositionIds: [posId, posId, posId]}`.
4. Observe `RewardsPoolName` balance decreases by 3× the single-position bonus, not 1×.
5. Owner receives 3× bonus.

The invariant "each position's bonus segment is paid at most once per claim call" is violated because `claimRewardsAndUpdatesPositions` processes N pre-loaded stale snapshots of the same position, each independently computing and paying the full current-segment bonus before the prior write-back is visible to the next snapshot. [8](#0-7) [9](#0-8)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-215)
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-241)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/validator_events.go (L85-101)
```go
func (k Keeper) decrementEventRefCount(ctx context.Context, valAddr sdk.ValAddress, seq uint64) error {
	key := collections.Join(valAddr, seq)
	event, err := k.ValidatorEvents.Get(ctx, key)
	if errors.Is(err, collections.ErrNotFound) {
		return nil // already cleaned up
	}
	if err != nil {
		return err
	}

	if event.ReferenceCount <= 1 {
		return k.ValidatorEvents.Remove(ctx, key)
	}

	event.ReferenceCount--
	return k.ValidatorEvents.Set(ctx, key, event)
}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L175-181)
```go
func (k Keeper) validateClaimRewards(pos types.Position, owner string) error {
	if !pos.IsOwner(owner) {
		return types.ErrNotPositionOwner
	}

	return nil
}
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
