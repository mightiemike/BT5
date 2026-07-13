### Title
Bonus Checkpoint Advances Without Payment When Bonus Pool Is Drained During Redelegation Slash — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` mutates the position's checkpoints in-memory **and** writes decremented event reference counts to the store **before** checking `sufficientBonusPoolBalance`. When `ErrInsufficientBonusPool` is returned, `slashRedelegationPosition` swallows the error and then calls `setPositionWithState` with the already-mutated position, persisting the advanced checkpoints with no bonus payment. The owner permanently loses the accrued bonus for the elapsed bonded segment.

---

### Finding Description

**Step 1 — Entry point.**
`BeforeRedelegationSlashed` (hooks.go:128) is a standard Cosmos SDK staking hook fired during `SlashRedelegation`. It directly calls `slashRedelegationPosition`. [1](#0-0) 

**Step 2 — `processEventsAndClaimBonus` mutates state before the pool check.**

Inside the event loop (lines 172–198), two irreversible side-effects occur **before** `sufficientBonusPoolBalance` is ever reached:

1. **In-memory checkpoint advance** — `pos.UpdateLastEventSeq(entry.Seq)` is called on the pointer-receiver `pos *types.PositionState` for every event processed.
2. **Persistent store write** — `k.decrementEventRefCount(ctx, valAddr, entry.Seq)` writes to the `ValidatorEvents` collection, potentially deleting events whose `ReferenceCount` drops to zero. [2](#0-1) 

After the loop, `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` further advance the in-memory checkpoints: [3](#0-2) 

Only then is the pool balance checked: [4](#0-3) 

If the pool is insufficient, the function returns `nil, ErrInsufficientBonusPool` — but `pos` is already mutated and the store ref-count decrements are already committed to the CacheMultiStore.

**Step 3 — Error is swallowed in `slashRedelegationPosition`.** [5](#0-4) 

The `ErrInsufficientBonusPool` branch logs and continues. The function does **not** restore `pos` to its pre-call state.

**Step 4 — Mutated position is persisted.**

For a partial slash, `setPositionWithState` is called with the already-mutated `pos`: [6](#0-5) 

`setPositionWithState` writes `pos.Position` (with advanced `LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) to the `Positions` collection: [7](#0-6) 

**Step 5 — Permanent loss.**

The next call to `processEventsAndClaimBonus` for this position starts from the advanced `LastEventSeq`. The events that were already processed (and whose ref counts were decremented — possibly to zero, causing deletion) are invisible to future replays. The bonus for the elapsed bonded segment is permanently unrecoverable. [8](#0-7) 

---

### Impact Explanation

A position owner loses all accrued bonus rewards for the bonded segment that was processed during the slash hook. The loss is permanent: checkpoints are advanced, event records are decremented (and potentially deleted), and no coins are transferred. This is a direct economic loss to the position owner, matching the High scope (bonus reward loss with no recovery path).

---

### Likelihood Explanation

The precondition — bonus pool drained below the owed amount at the moment `BeforeRedelegationSlashed` fires — is reachable in production. The `RewardsPoolName` module account balance depends on external funding and concurrent claims. A validator double-sign slash during a period of high bonus claim activity, or a deliberately underfunded pool, can trigger this path. No governance or privileged access is required; the hook fires automatically from the staking module's evidence/slash logic.

---

### Recommendation

Move all in-memory checkpoint mutations and store ref-count decrements to **after** the `sufficientBonusPoolBalance` check succeeds, or — preferably — restructure `processEventsAndClaimBonus` to compute the total bonus first (read-only pass), check the pool, and only then apply mutations and store writes. Alternatively, if the pool is insufficient, `slashRedelegationPosition` must explicitly roll back `pos` to its pre-call snapshot before calling `setPositionWithState`, and must not call `decrementEventRefCount` for events that were not paid out.

---

### Proof of Concept

```go
// Keeper test outline (unmodified Go/Cosmos test setup):
// 1. Create a tier position delegated to a bonded validator.
// 2. Advance several blocks so bonus accrues.
// 3. Drain the RewardsPoolName module account to zero
//    (send all coins out via bankKeeper.SendCoinsFromModuleToAccount in test setup).
// 4. Register a redelegation mapping for the position's unbonding ID.
// 5. Call hooks.BeforeRedelegationSlashed(ctx, unbondingID, partialShares).
// 6. Assert:
//    a. ownerBalance has NOT increased (no bonus paid).
//    b. pos.LastBonusAccrual == blockTime (checkpoint advanced).
//    c. pos.LastEventSeq == latestEventSeq (checkpoint advanced).
//    d. ValidatorEvents ref counts were decremented (events may be deleted).
// This proves the invariant is broken: checkpoint advanced, no payment made.
```

### Citations

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-198)
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-217)
```go
	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L54-64)
```go
	if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
		// Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt.
		if errors.Is(err, types.ErrInsufficientBonusPool) {
			k.logger(ctx).Error("insufficient bonus pool during redelegation slash",
				"position_id", pos.Id,
				"error", err.Error(),
			)
		} else {
			return err
		}
	}
```

**File:** x/tieredrewards/keeper/slash.go (L76-77)
```go
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** x/tieredrewards/keeper/position.go (L139-141)
```go
	if err := k.Positions.Set(ctx, pos.Id, pos); err != nil {
		return err
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
