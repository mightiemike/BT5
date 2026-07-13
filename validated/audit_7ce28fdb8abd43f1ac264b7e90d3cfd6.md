### Title
Bonus Reward Checkpoints Advanced Without Fund Transfer During Insufficient Pool Slash — (`x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the error is deliberately swallowed to prevent a chain halt. However, `processEventsAndClaimBonus` mutates the position's reward checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) **before** the pool-balance check. The position is then persisted with those advanced checkpoints via `setPositionWithState`, permanently erasing the user's accrued bonus entitlement without ever transferring the coins.

---

### Finding Description

`processEventsAndClaimBonus` in `x/tieredrewards/keeper/claim_rewards.go` follows this sequence:

1. Iterates over validator events, calling `pos.UpdateLastEventSeq(entry.Seq)` and `k.decrementEventRefCount(...)` for each — **mutating `pos` and consuming event references in-place**.
2. Calls `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` and `pos.UpdateLastKnownBonded(bonded)` — **further mutating `pos`**.
3. Only then calls `k.sufficientBonusPoolBalance(ctx, bonusCoins)` — returning `ErrInsufficientBonusPool` if the pool is short.
4. Only if the pool check passes does it call `k.bankKeeper.SendCoinsFromModuleToAccount(...)`. [1](#0-0) [2](#0-1) 

In `slashRedelegationPosition`, the call to `processEventsAndClaimBonus` is wrapped in an error handler that **swallows** `ErrInsufficientBonusPool`:

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // error swallowed — pos already mutated
    } else {
        return err
    }
}
``` [3](#0-2) 

After the swallowed error, execution continues and `setPositionWithState` is called with the already-mutated `pos`: [4](#0-3) 

The result is that the position's checkpoints are persisted as if the bonus was successfully paid, but the coins were never transferred. Because `decrementEventRefCount` was also called inside the loop, the underlying validator events may be garbage-collected, making the lost bonus **unrecoverable** even by a future manual correction. [5](#0-4) 

---

### Impact Explanation

A tiered-rewards position holder whose validator is slashed while the bonus pool is depleted permanently loses all accrued bonus rewards for the period up to the slash. The position's `LastEventSeq` and `LastBonusAccrual` are advanced to the current block, so no future `claimRewards` call can recover the lost amount. The corrupted value is the `bonusCoins` that should have been transferred from `types.RewardsPoolName` to the position owner. [6](#0-5) 

---

### Likelihood Explanation

The trigger requires two concurrent conditions: (a) a validator slash event affecting a position with a redelegation entry, and (b) the bonus pool balance being insufficient at that moment. Validator slashes are normal protocol events triggered by evidence submission (any unprivileged account can submit `MsgSubmitEvidence`). The bonus pool can be depleted by high claim volume or delayed replenishment. Both conditions are realistic in production. [7](#0-6) 

---

### Recommendation

Separate the checkpoint-advancement logic from the coin-transfer logic. Either:

1. **Do not mutate `pos` before confirming the pool is sufficient** — check `sufficientBonusPoolBalance` before the event loop, or snapshot the pre-loop state and restore it on error; or
2. **Do not persist the mutated position when the pool is insufficient** — if `ErrInsufficientBonusPool` is swallowed, revert `pos` to its pre-call state before calling `setPositionWithState`, so the bonus period remains claimable once the pool is replenished.

Additionally, `decrementEventRefCount` should not be called for events whose bonus was not actually paid, to preserve the ability to re-process them. [8](#0-7) 

---

### Proof of Concept

1. Alice holds a tiered-rewards position delegated to validator V, with a redelegation entry (unbonding ID `U`).
2. The `tieredrewards` bonus pool is at or near zero (e.g., drained by prior claims).
3. An unprivileged account submits double-sign evidence against V, triggering a slash.
4. The staking module calls `BeforeRedelegationSlashed` → `slashRedelegationPosition(ctx, U, sharesToUnbond)`.
5. `processEventsAndClaimBonus` iterates all pending events for V since Alice's `LastEventSeq`, advancing `pos.LastEventSeq`, `pos.LastBonusAccrual`, and decrementing event ref counts.
6. `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`; no coins are sent.
7. The error is swallowed; `setPositionWithState` persists `pos` with the advanced checkpoints.
8. Alice's next `MsgClaimRewards` computes zero bonus for the already-advanced period. The accrued bonus is permanently lost. [9](#0-8) [10](#0-9)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L161-241)
```go
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
