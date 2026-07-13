### Title
Hardcoded `lastKnownBonded=true` in `TierRedelegate` Causes Bonus Overclaim for Unbonded Destination Validator Periods — (`x/tieredrewards/keeper/msg_server.go`)

### Summary

`TierRedelegate` unconditionally calls `pos.UpdateBonusCheckpoints(latestSeq, blockTime, true)` with a hardcoded `lastKnownBonded=true`, regardless of the destination validator's actual bonded status. When the destination validator is currently unbonded at redelegate time, this false checkpoint causes `processEventsAndClaimBonus` to compute bonus rewards for the unbonded gap as if the validator were bonded, violating the invariant that bonus accrues only during bonded segments.

---

### Finding Description

**Root cause — `msg_server.go` line 263:**

```go
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)  // hardcoded true
``` [1](#0-0) 

`UpdateBonusCheckpoints` writes three fields atomically:

```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
    p.LastEventSeq = lastEventSeq
    p.LastBonusAccrual = t
    p.LastKnownBonded = lastKnownBonded
}
``` [2](#0-1) 

`latestSeq` is the destination validator's current latest event sequence. If the destination validator is unbonded, its last recorded event was an UNBOND event at some `seq=N`. Setting `LastEventSeq=N` means that UNBOND event will never be replayed. Setting `LastKnownBonded=true` means the next replay starts with `bonded=true` — the opposite of reality.

**How the overclaim materialises — `claim_rewards.go` lines 164–179:**

```go
bonded := pos.LastKnownBonded   // true (wrong)
segmentStart := pos.LastBonusAccrual  // redelegate blockTime

for _, entry := range events {
    if bonded {
        bonus := k.computeSegmentBonus(...)  // pays bonus for this segment
        totalBonus = totalBonus.Add(bonus)
    }
    switch evt.EventType {
    case UNBOND: bonded = false
    case BOND:   bonded = true
    }
    segmentStart = evt.Timestamp
}
``` [3](#0-2) 

When the destination validator later bonds (BOND event at seq=N+1), `processEventsAndClaimBonus` sees `bonded=true` and computes bonus for the segment `[redelegateTime, bondEventTime]` — the entire unbonded gap — before processing the BOND event. The "defensive" check at line 206 only guards the **final open segment** after all events are consumed; it does not protect historical segments computed inside the loop. [4](#0-3) 

The `unbondingID=0` branch (source validator unbonded → instant redelegate) is one concrete path to reach this state, but the bug is present for **any** redelegate to an unbonded destination validator, regardless of whether `unbondingID` is zero. [5](#0-4) 

---

### Impact Explanation

The attacker overclaims bonus rewards from the `RewardsPoolName` module account for the period `[redelegateTime, dstValidatorBondTime]`. The overclaimed amount scales with position size (`Delegation.Shares × tokensPerShare × tier.BonusApy × unbondedDuration`). This is a direct, quantifiable drain of the on-chain rewards pool.

---

### Likelihood Explanation

Any position owner can execute this by:
1. Identifying an unbonded destination validator (common during validator churn).
2. Calling `MsgTierRedelegate` to that validator.
3. Waiting for the validator to re-bond.
4. Calling `MsgClaimTierRewards`.

No privileged access, governance action, or leaked keys are required. The `validateRedelegatePosition` function imposes no restriction on the destination validator's bonded status. [6](#0-5) 

---

### Recommendation

Replace the hardcoded `true` with a live check of the destination validator's bonded status:

```go
dstVal, err := ms.stakingKeeper.GetValidator(ctx, dstValAddr)
if err != nil {
    return nil, err
}
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), dstVal.IsBonded())
```

This mirrors the logic already used correctly in `processEventsAndClaimBonus` at line 206.

---

### Proof of Concept

```
1. Create position on validator A (bonded).
2. Validator B unbonds (UNBOND event recorded at seq=5 for B).
3. Call MsgTierRedelegate: src=A, dst=B.
   → unbondingID may be 0 (if A is also unbonded) or non-zero.
   → pos.LastKnownBonded = true, pos.LastEventSeq = 5, pos.LastBonusAccrual = T1.
4. Advance block time by Δt (e.g., 30 days). Validator B remains unbonded.
5. Validator B re-bonds (BOND event at seq=6, timestamp=T2=T1+Δt).
6. Call MsgClaimTierRewards.
   → processEventsAndClaimBonus: bonded=true, segmentStart=T1.
   → BOND event at T2: bonded=true → computeSegmentBonus([T1,T2]) → pays Δt of bonus.
   → Expected: zero bonus for [T1,T2] (validator was unbonded).
   → Actual: full bonus paid for Δt unbonded period.
``` [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L249-255)
```go
	// unbondingID 0 means the source validator is unbonded; redelegation is instant.
	// We skip mapping because no asynchronous completion hook will trigger.
	if unbondingID != 0 {
		if err := ms.setRedelegationMapping(ctx, unbondingID, pos.Id); err != nil {
			return nil, err
		}
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L257-263)
```go
	latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
```

**File:** x/tieredrewards/types/position.go (L65-69)
```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
	p.LastEventSeq = lastEventSeq
	p.LastBonusAccrual = t
	p.LastKnownBonded = lastKnownBonded
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L162-199)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L205-213)
```go
	// Defensive: validator bond status check
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L67-103)
```go
func (k Keeper) validateRedelegatePosition(ctx context.Context, pos types.PositionState, owner, dstValidator string) error {
	if !pos.IsOwner(owner) {
		return types.ErrNotPositionOwner
	}

	if !pos.IsDelegated() {
		return types.ErrPositionNotDelegated
	}

	if pos.Delegation.ValidatorAddress == dstValidator {
		return types.ErrRedelegationToSameValidator
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if pos.HasTriggeredExit() && pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationElapsed
	}

	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return err
	}

	if tier.IsCloseOnly() {
		return types.ErrTierIsCloseOnly
	}

	isRedelegating, err := k.isRedelegating(ctx, pos.DelegatorAddress)
	if err != nil {
		return err
	}
	if isRedelegating {
		return errorsmod.Wrapf(types.ErrActiveRedelegation, "position %d has an active redelegation", pos.Id)
	}

	return nil
}
```
