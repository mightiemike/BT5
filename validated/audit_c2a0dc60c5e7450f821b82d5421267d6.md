### Title
Permanent Bonus Reward Loss via Silent `ErrInsufficientBonusPool` Swallow in `slashRedelegationPosition` - (File: `x/tieredrewards/keeper/slash.go`)

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is deliberately swallowed. However, by the time that error is returned, `processEventsAndClaimBonus` has already (1) advanced the position's in-memory bonus checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) and (2) written event reference-count decrements to the KV store. `slashRedelegationPosition` then calls `setPositionWithState` with the mutated position, persisting the advanced checkpoints. The position owner permanently loses the accrued bonus for those events even after the pool is replenished.

### Finding Description

`processEventsAndClaimBonus` performs all checkpoint mutations and state writes **before** the pool balance check:

- Inside the event loop (lines 193, 196): `pos.UpdateLastEventSeq(entry.Seq)` (in-memory) and `k.decrementEventRefCount(ctx, valAddr, entry.Seq)` (KV-store write) are called for every event.
- After the loop (lines 215–217): `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` advance the accrual timestamp and bonded-state checkpoint in memory.
- Only then (line 230) is `sufficientBonusPoolBalance` checked; if the pool is short it returns `ErrInsufficientBonusPool`. [1](#0-0) [2](#0-1) 

Back in `slashRedelegationPosition`, the `ErrInsufficientBonusPool` branch logs the error and falls through — it does **not** return, so execution continues with the already-mutated `pos`: [3](#0-2) 

For a **partial slash** the function then calls `setPositionWithState` with the mutated `pos`, persisting the advanced checkpoints: [4](#0-3) 

For a **full slash** `ClearBonusCheckpoints()` is called first, so the in-memory checkpoint advancement is overwritten — but the KV-store event reference-count decrements already written inside the loop are **not** rolled back, potentially causing premature event garbage-collection for other positions that share those events.

### Impact Explanation

For a partial redelegation slash occurring when the bonus pool is empty or insufficient:

- The position's `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded` are persisted as if the bonus was successfully claimed.
- The bonus is never paid.
- Even after the pool is replenished, the position cannot replay those events (checkpoints already advanced past them), so the accrued bonus is permanently lost.
- The corrupted values are the position's bonus-accounting fields stored in `k.Positions` and the event `ReferenceCount` entries in `k.ValidatorEvents`.

### Likelihood Explanation

The bonus pool (`RewardsPoolName`) is a finite module account funded by governance or external deposits. It can legitimately reach zero after sustained reward claims. Validator slashes during redelegation periods are a normal protocol event. No privileged access is required: any user who holds a tier position with an active redelegation is affected whenever a slash fires while the pool is short. An adversary who can drain the pool (e.g., by holding many positions and claiming rewards) can then trigger or wait for a slash to cause permanent bonus loss for other users.

### Recommendation

Wrap the `processEventsAndClaimBonus` call in `slashRedelegationPosition` with a cache context so that KV-store side-effects (event reference-count decrements) are discarded when the pool is insufficient:

```go
cacheCtx, write :=

### Citations

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-231)
```go
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

**File:** x/tieredrewards/keeper/slash.go (L66-77)
```go
	fullSlash := sharesToUnbond.GTE(pos.Delegation.Shares)

	if fullSlash {
		pos.Delegation = nil
		pos.ClearBonusCheckpoints()
		return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
	}
	// In-memory only: the persisted Position carries no share count, and the
	// live delegation will reflect the post-Unbond shares on the next read.
	// Update the local copy so any follow-up logic in this call sees consistent state.
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```
