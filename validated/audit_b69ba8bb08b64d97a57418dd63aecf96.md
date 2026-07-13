### Title
Bonus Reward Checkpoints Advanced Before Pool Transfer in `slashRedelegationPosition`, Causing Permanent Bonus Loss on Insufficient Pool - (File: `x/tieredrewards/keeper/slash.go`)

### Summary

In `slashRedelegationPosition`, when the bonus pool is insufficient during a redelegation slash, the function swallows `ErrInsufficientBonusPool` and persists the position with already-advanced accrual checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`). Because `processEventsAndClaimBonus` advances these checkpoints **before** the pool balance check, the user permanently loses the bonus rewards that accrued up to the slash block — they can never be reclaimed.

### Finding Description

`processEventsAndClaimBonus` takes `pos *types.PositionState` (a pointer) and mutates it in-place in two distinct phases:

**Phase 1 — checkpoint advancement (lines 193, 215, 217 of `claim_rewards.go`):**

```go
pos.UpdateLastEventSeq(entry.Seq)          // line 193 — inside event loop
// ...
applyBonusAccrualCheckpoint(&pos.Position, blockTime)  // line 215
pos.UpdateLastKnownBonded(bonded)                       // line 217
```

**Phase 2 — pool check and transfer (lines 230, 239):**

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err   // returns error — but pos is already mutated
}
// ...
k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins)
```

When the pool check fails, the function returns `ErrInsufficientBonusPool` with `pos` already carrying the advanced checkpoints.

In `slashRedelegationPosition` (`slash.go` lines 54–77), this error is deliberately swallowed:

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // continues — pos still has advanced checkpoints
    } else {
        return err
    }
}
// ...
return k.setPositionWithState(ctx, pos, nil)   // persists advanced checkpoints
```

`setPositionWithState` then writes the position with the advanced `LastBonusAccrual`, `LastEventSeq`, and `LastKnownBonded` to the store. On the next `processEventsAndClaimBonus` call for this position, the replay starts from the advanced checkpoint, skipping the period that was never paid.

### Impact Explanation

**Impact: High.** The user permanently loses all bonus rewards that accrued between the previous `LastBonusAccrual` and the slash block time. The amount is proportional to position size × `BonusApy` × elapsed duration. For a large position with a long accrual gap (e.g., 365 days at 4% APY on a 1 000 000 token position), the loss can be substantial. There is no recovery path: the checkpoints cannot be rolled back by any user-accessible message.

The corrupted invariant is: `LastBonusAccrual` / `LastEventSeq` / `LastKnownBonded` must only advance when the corresponding bonus coins have been successfully transferred to the owner.

### Likelihood Explanation

**Likelihood: Low.** Three conditions must coincide:
1. A tier position must be in an active redelegation state (i.e., `MsgTierRedelegate` was called and the redelegation entry has not yet completed).
2. The destination validator must be slashed during the redelegation window, triggering `BeforeRedelegationSlashed`.
3. The `tieredrewards` bonus pool must be empty or insufficient to cover the accrued bonus at that exact block.

Condition 3 is the most constraining. However, the pool can be empty if it was never funded, was drained by concurrent claims, or if the BeginBlocker top-up has not yet run. Validator slashing is a normal protocol event, and redelegation windows are typically 21 days, giving a meaningful exposure window.

### Recommendation

Move the pool balance check **before** mutating the position's checkpoints, or restructure `processEventsAndClaimBonus` to only advance checkpoints after a successful transfer. Concretely, the simplest fix is to compute `totalBonus`, check `sufficientBonusPoolBalance`, and only then call `applyBonusAccrualCheckpoint` / `UpdateLastEventSeq` / `UpdateLastKnownBonded`. This ensures that if the pool check fails, the position's checkpoints remain at their pre-call values, and the error propagation in `slashRedelegationPosition` (or any other caller) cannot silently advance them.

Alternatively, if the "forfeit silently on insufficient pool" behavior is intentional for chain-halt avoidance, the checkpoints should be advanced only to the point of the last successfully paid segment, not to `blockTime`.

### Proof of Concept

1. User calls `MsgLockTier` to create a position on validator V1.
2. User calls `MsgTierRedelegate` to move the position to validator V2. A `RedelegationMapping[unbondingId → positionId]` entry is created.
3. 30 days pass. Bonus accrues but is not claimed. The bonus pool is empty (e.g., never funded or drained).
4. V2 is slashed. Staking fires `BeforeRedelegationSlashed(unbondingId, sharesToUnbond)`.
5. `slashRedelegationPosition` is called. It calls `processEventsAndClaimBonus(&pos)`.
6. Inside `processEventsAndClaimBonus`: `LastBonusAccrual` is advanced to `blockTime` (line 215), `LastEventSeq` is advanced (line 193), `LastKnownBonded` is updated (line 217). Then `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` (line 230). The function returns the error with `pos` already mutated.
7. Back in `slashRedelegationPosition`: the error is matched as `ErrInsufficientBonusPool` (line 56), logged, and execution continues.
8. `setPositionWithState(ctx, pos, nil)` (line 77) persists the position with the advanced checkpoints.
9. The user later calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts from the advanced `LastBonusAccrual` — the 30-day accrual window is gone. The user receives 0 bonus for that period. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** x/tieredrewards/keeper/slash.go (L53-64)
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L192-217)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-232)
```go
	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L15-21)
```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
	accrualEnd := blockTime
	if pos.CompletedExitLockDuration(blockTime) {
		accrualEnd = pos.ExitUnlockAt
	}
	pos.UpdateLastBonusAccrual(accrualEnd)
}
```

**File:** x/tieredrewards/types/position.go (L65-103)
```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
	p.LastEventSeq = lastEventSeq
	p.LastBonusAccrual = t
	p.LastKnownBonded = lastKnownBonded
}

func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
	p.ExitTriggeredAt = blockTime
	p.ExitUnlockAt = blockTime.Add(duration)
}

func (p *Position) UpdateLastBonusAccrual(t time.Time) {
	p.LastBonusAccrual = t
}

func (p *Position) ClearBonusCheckpoints() {
	p.LastBonusAccrual = time.Time{}
	p.LastEventSeq = 0
	p.LastKnownBonded = false
}

func (p Position) HasTriggeredExit() bool {
	return !p.ExitTriggeredAt.IsZero()
}

func (p Position) IsOwner(address string) bool {
	return p.Owner == address
}

func (p Position) ExitWithFullDelegation(amount, positionAmount math.Int) bool {
	return amount.Equal(positionAmount)
}

func (p *Position) UpdateLastEventSeq(seq uint64) {
	p.LastEventSeq = seq
}

func (p *Position) UpdateLastKnownBonded(bonded bool) {
	p.LastKnownBonded = bonded
```
