### Title
Bonus Rewards Permanently Lost When Pool Is Insufficient During Redelegation Slash — (`x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is deliberately swallowed to prevent a chain halt. However, `processEventsAndClaimBonus` mutates the position's checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) **before** it checks pool sufficiency. The caller then persists the mutated position state. The result is that the position's accounting advances as if the bonus was paid, but the owner never receives the coins — permanently destroying the accrued bonus for that period.

---

### Finding Description

`processEventsAndClaimBonus` in `claim_rewards.go` processes all pending validator events in a loop, advancing `pos.LastEventSeq` and decrementing on-chain reference counts per event. After the loop it calls `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` to advance the time-based checkpoints. Only **after** all of these mutations does it check whether the bonus pool holds enough balance: [1](#0-0) [2](#0-1) [3](#0-2) 

When `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`, the function returns an error with `pos` already fully mutated — checkpoints advanced, event seq updated, ref counts decremented — but no coins sent.

Back in `slashRedelegationPosition`, this error is explicitly caught and swallowed: [4](#0-3) 

Execution then continues and `setPositionWithState` is called with the already-mutated `pos`, persisting the advanced checkpoints to the store: [5](#0-4) 

Because `LastBonusAccrual` is now at the current block time and `LastEventSeq` is at the latest event, there is no mechanism to re-process the skipped period. The bonus for the entire segment from the previous checkpoint to the slash block is permanently unrecoverable.

---

### Impact Explanation

**Impact: Medium.** Delegators with active redelegations permanently lose accrued bonus rewards whenever a validator slash coincides with an insufficient bonus pool. The principal delegation is unaffected, but the bonus CRO owed for the elapsed bonded period is silently discarded. The corrupted value is the position's `LastBonusAccrual` / `LastEventSeq` checkpoints, which advance past unpaid bonus segments. [6](#0-5) 

---

### Likelihood Explanation

**Likelihood: Medium.** Two independent conditions must coincide: (1) a delegator has an active redelegation entry when a validator is slashed — a normal staking event triggered by any double-sign or downtime infraction — and (2) the `RewardsPool` module account balance is below the computed bonus at that instant. The bonus pool can be depleted by normal reward distribution over time, especially if the pool is not topped up frequently. Neither condition requires privileged access or attacker control; both arise from ordinary protocol operation. [7](#0-6) 

---

### Recommendation

Move the `sufficientBonusPoolBalance` check **before** `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` inside `processEventsAndClaimBonus`. This way, if the pool is insufficient, the position's checkpoints are not advanced and the owner can claim the bonus once the pool is replenished. Alternatively, if the intent is to skip payment and still advance checkpoints, the position should be saved with a flag or the skipped amount should be recorded so it can be paid retroactively. [8](#0-7) 

---

### Proof of Concept

1. Delegator calls `TierRedelegate` — an active redelegation entry is created from validator A to validator B.
2. Validator A commits a double-sign infraction; the staking module calls `SlashRedelegation`.
3. `BeforeRedelegationSlashed` fires → `slashRedelegationPosition` is called with the unbonding ID.
4. Inside `processEventsAndClaimBonus`:
   - The event loop runs, advancing `pos.LastEventSeq` and decrementing on-chain ref counts for all pending events.
   - `applyBonusAccrualCheckpoint` sets `pos.LastBonusAccrual = blockTime`.
   - `pos.UpdateLastKnownBonded(bonded)` is called.
   - `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` — no coins are sent.
5. `slashRedelegationPosition` swallows the error (line 56–63) and calls `setPositionWithState` with the mutated `pos`.
6. The position is now stored with `LastBonusAccrual` at the slash block and `LastEventSeq` at the latest event.
7. On the delegator's next `ClaimTierRewards`, `processEventsAndClaimBonus` starts from the new checkpoint — the entire bonus period before the slash is permanently lost. [9](#0-8) [10](#0-9)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L193-196)
```go
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L211-232)
```go
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
```

**File:** x/tieredrewards/keeper/slash.go (L19-50)
```go
func (k Keeper) slashRedelegationPosition(ctx context.Context, unbondingId uint64, sharesToUnbond math.LegacyDec) error {
	positionId, err := k.getRedelegationMapping(ctx, unbondingId)
	if errors.Is(err, collections.ErrNotFound) {
		return nil
	}
	if err != nil {
		return err
	}

	pos, err := k.getPositionState(ctx, positionId)
	if errors.Is(err, types.ErrPositionNotFound) {
		k.logger(ctx).Error("position not found during redelegation slash",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"error", err.Error(),
		)
		return nil
	}
	if err != nil {
		return err
	}

	if !pos.IsDelegated() {
		// Defensive
		k.logger(ctx).Error("delegation missing during BeforeRedelegationSlashed",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"shares_to_unbond", sharesToUnbond.String(),
		)
		return nil
	}

```

**File:** x/tieredrewards/keeper/slash.go (L54-77)
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

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```
