The key code is already visible. Let me verify the exact ordering of checkpoint mutations vs. the pool balance check in `processEventsAndClaimBonus`.

The code is fully visible. Let me trace the exact execution path to confirm the invariant violation.

**Execution trace in `processEventsAndClaimBonus` (claim_rewards.go):**

- **Line 193**: `pos.UpdateLastEventSeq(entry.Seq)` — mutates `pos.LastEventSeq` in-place, inside the event loop
- **Line 196**: `k.decrementEventRefCount(ctx, valAddr, entry.Seq)` — **persistent store write** (ref counts decremented)
- **Line 215**: `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` — mutates `pos.LastBonusAccrual` in-place
- **Line 217**: `pos.UpdateLastKnownBonded(bonded)` — mutates `pos.LastKnownBonded` in-place
- **Line 230**: `sufficientBonusPoolBalance` check — only NOW checks pool; returns `ErrInsufficientBonusPool` with all three checkpoints already advanced and ref counts already decremented

**Execution trace in `slashRedelegationPosition` (slash.go):**

- **Lines 54–64**: `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`; the error is swallowed (only logged), execution continues with the mutated `pos`
- **Line 71 or 77**: `setPositionWithState(ctx, pos, ...)` — persists the advanced checkpoints to the store

The invariant stated in ADR-006 line 353 — "Bonus cannot be double-claimed: `LastEventSeq` prevents event replay, `LastBonusAccrual` prevents segment replay" — is violated in the opposite direction: checkpoints advance without payment, making the bonus permanently unclaimable.

---

### Title
Permanent Bonus Loss via Checkpoint Advancement Without Payment in `slashRedelegationPosition` — (`x/tieredrewards/keeper/slash.go`)

### Summary
When `BeforeRedelegationSlashed` fires and the `RewardsPoolName` account balance is below the accrued bonus, `processEventsAndClaimBonus` advances all three reward checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) and decrements event reference counts **before** checking pool sufficiency. `slashRedelegationPosition` then silently swallows `ErrInsufficientBonusPool` and persists the advanced checkpoints via `setPositionWithState`, causing the position owner to permanently lose all bonus accrued up to the slash event.

### Finding Description

In `processEventsAndClaimBonus` (`x/tieredrewards/keeper/claim_rewards.go`):

1. The event loop (lines 172–199) calls `pos.UpdateLastEventSeq(entry.Seq)` and `k.decrementEventRefCount(...)` for every pending event — both in-memory and persistent store mutations.
2. After the loop, `applyBonusAccrualCheckpoint` (line 215) and `pos.UpdateLastKnownBonded` (line 217) advance the remaining two checkpoints.
3. Only at line 230 does `sufficientBonusPoolBalance` run. If it fails, `ErrInsufficientBonusPool` is returned — but all three checkpoints are already mutated and event ref counts are already decremented in the store. [1](#0-0) 

In `slashRedelegationPosition` (`x/tieredrewards/keeper/slash.go`):

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error(...)   // swallowed
    } else {
        return err
    }
}
// pos.LastEventSeq, pos.LastBonusAccrual, pos.LastKnownBonded are all advanced
// but zero coins were transferred to the owner
return k.setPositionWithState(ctx, pos, nil)  // persists advanced checkpoints
``` [2](#0-1) 

The next `ClaimTierRewards` call reads the persisted advanced checkpoints and computes zero bonus for the already-advanced period, making the loss permanent. [3](#0-2) 

A secondary consequence: `decrementEventRefCount` is called inside the loop before the pool check fails, so event reference counts are permanently decremented even though the position never received payment for those events. [4](#0-3) 

### Impact Explanation

A position owner whose redelegation entry is slashed while the rewards pool is below their accrued bonus permanently loses all bonus accrued since their last checkpoint. The loss is proportional to the position size, the tier's `BonusApy`, and the time elapsed since the last claim. For large positions or long accrual periods, this can be material. The ADR-006 security invariant — "Bonus cannot be double-claimed: `LastEventSeq` prevents event replay, `LastBonusAccrual` prevents segment replay" — is violated in the opposite direction: checkpoints advance without payment. [5](#0-4) 

### Likelihood Explanation

The precondition (pool balance below accrued bonus) is a normal operational state: the pool is funded by governance and depleted by user claims. The trigger (redelegation slash) is a standard Cosmos SDK staking event requiring no attacker — validator downtime or double-signing are routine. Both conditions can co-occur naturally without any privileged actor. An adversary who can drain the pool (by claiming their own legitimate rewards) and then wait for a natural slash event can reliably trigger this for targeted positions.

### Recommendation

Move the `sufficientBonusPoolBalance` check **before** any checkpoint mutation or ref-count decrement. If the pool is insufficient, return `ErrInsufficientBonusPool` with `pos` unmodified. In `slashRedelegationPosition`, when swallowing `ErrInsufficientBonusPool`, do **not** call `setPositionWithState` with the (unmodified) `pos` — or explicitly reset the checkpoints to their pre-call values before persisting. This preserves the invariant that checkpoints advance only when coins are actually transferred.

### Proof of Concept

1. Create a tier position and redelegate it to a second validator (establishing a `RedelegationMappings` entry).
2. Advance block time by 30 days so bonus accrues.
3. Drain `RewardsPoolName` to zero (or below the accrued bonus) via `ClaimTierRewards` on other positions.
4. Call `keeper.Hooks().BeforeRedelegationSlashed(ctx, unbondingID, sharesToUnbond)`.
5. Assert: owner balance did not increase; `pos.LastBonusAccrual` advanced to block time; `pos.LastEventSeq` advanced.
6. Replenish the pool. Call `MsgClaimTierRewards` for the position.
7. Assert: bonus returned is zero — the accrued period is permanently lost. [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L153-165)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L193-231)
```go
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
```

**File:** x/tieredrewards/keeper/slash.go (L53-77)
```go
	// Settle bonus against PRE-slash shares.
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

**File:** doc/architecture/adr-006.md (L349-353)
```markdown
- Pool balance is never exceeded. User paths fail atomically; the `BeforeRedelegationSlashed` hook forfeits bonus silently if the pool is short, to avoid chain halt.
- Base-reward routing: `SetWithdrawAddr(posDelAddr, owner)` is installed at position creation and cleared on deletion, so every base-reward withdrawal lands on the owner.
- Auth-account materialisation: before the first delegate or undelegate on a position's delegator address, a `BaseAccount` must exist at that address. Both `MsgLockTier` and `MsgCommitDelegationToTier` reserve the account up front via `createPositionDelegatorAccount`.
- CloseOnly tiers block new positions while allowing exits.
- Bonus cannot be double-claimed: `LastEventSeq` prevents event replay, `LastBonusAccrual` prevents segment replay, `LastKnownBonded` prevents unbonded gap overpay.
```
