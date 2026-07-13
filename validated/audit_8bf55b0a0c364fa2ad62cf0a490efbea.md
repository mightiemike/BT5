### Title
Cross-Segment Bonus Accrual Checkpoint Failure in `ClearPosition` Lets Users Extract Unauthorized Bonus Rewards — (`x/tieredrewards/keeper/bonus_rewards.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`applyBonusAccrualCheckpoint` sets `LastBonusAccrual` to `ExitUnlockAt` (not to the current block time) when a position's exit lock duration has elapsed. After `MsgClearPosition` resets `ExitUnlockAt` to zero, the stale `LastBonusAccrual` checkpoint causes the next `processEventsAndClaimBonus` call to compute bonus for the gap between `ExitUnlockAt` and the `ClearPosition` block time — a period during which bonus was explicitly stopped by protocol design.

---

### Finding Description

The bonus reward system uses a lazy segment-based accounting model. `LastBonusAccrual` marks the start of the next unprocessed accrual segment. At the end of every `processEventsAndClaimBonus` call, `applyBonusAccrualCheckpoint` advances this checkpoint:

```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
    accrualEnd := blockTime
    if pos.CompletedExitLockDuration(blockTime) {
        accrualEnd = pos.ExitUnlockAt   // ← caps at ExitUnlockAt, not blockTime
    }
    pos.UpdateLastBonusAccrual(accrualEnd)
}
``` [1](#0-0) 

This is called unconditionally at line 215 of `processEventsAndClaimBonus`: [2](#0-1) 

When `blockTime >= ExitUnlockAt`, `accrualEnd` is pinned to `ExitUnlockAt` (call it **T2**), not to the current block time (**T3**). This is correct for a normal claim — it prevents re-computing bonus past the cap.

However, `MsgClearPosition` (per ADR-006) follows this sequence:

1. Call `claimRewards` → `processEventsAndClaimBonus` → sets `LastBonusAccrual = ExitUnlockAt` (T2)
2. Reset `ExitTriggeredAt = zero`, `ExitUnlockAt = zero`
3. Save position

After step 2, the position state is:
- `LastBonusAccrual = T2` (old `ExitUnlockAt`)
- `ExitUnlockAt = zero`

The next claim at **T4** calls `computeSegmentBonus` with `segmentStart = T2`, `segmentEnd = T4`. Because `ExitUnlockAt` is now zero, the guard `!pos.ExitUnlockAt.IsZero()` is false and no cap is applied:

```go
if !pos.ExitUnlockAt.IsZero() && segmentEnd.After(pos.ExitUnlockAt) {
    segmentEnd = pos.ExitUnlockAt
}
``` [3](#0-2) 

Bonus is therefore computed for the full interval **[T2, T4]**, including the **[T2, T3]** gap during which bonus was explicitly stopped because the exit commitment had elapsed.

The ADR states: *"Bonus stops when the exit commitment elapses."* The implementation violates this invariant for any `ClearPosition` call made after `ExitUnlockAt`. [4](#0-3) 

---

### Impact Explanation

A position owner can extract bonus rewards from the `RewardsPoolName` module account for time intervals during which the protocol explicitly stopped bonus accrual. The unauthorized payout is:

```
stolen_bonus = shares × tokensPerShare × bonusApy × (T3 − T2) / SecondsPerYear
```

For a large position (e.g., 1 000 000 bond tokens) at 4 % APY with a one-year gap between `ExitUnlockAt` and `ClearPosition`, the stolen bonus is approximately **40 000 tokens per cycle**. The attacker can repeat the cycle (trigger exit → wait for `ExitUnlockAt` → wait additional time D → `ClearPosition` → repeat) to drain the rewards pool proportionally to D and position size. The pool is a finite module account; repeated extraction degrades or eliminates bonus rewards for all legitimate position holders. [5](#0-4) 

---

### Likelihood Explanation

`MsgClearPosition` is a standard user-facing message callable by any position owner. ADR-006 explicitly permits it *"at any point during or after the exit commitment, as long as the position is delegated."* No privileged role, governance action, or special configuration is required. The attacker only needs a delegated position in a non-`CloseOnly` tier. The attack is deterministic and repeatable. [1](#0-0) 

---

### Recommendation

After `ClearPosition` resets `ExitTriggeredAt` and `ExitUnlockAt` to zero, explicitly advance `LastBonusAccrual` to the current block time. This closes the stale-checkpoint gap:

```go
// After resetting exit timestamps in ClearPosition:
pos.UpdateLastBonusAccrual(sdkCtx.BlockTime())
```

Alternatively, modify `applyBonusAccrualCheckpoint` to always set `LastBonusAccrual = blockTime` (relying solely on `computeSegmentBonus`'s per-segment cap at `ExitUnlockAt` to bound the paid amount), and add a post-reset checkpoint update in `ClearPosition`.

A regression test should:
1. Create a position, trigger exit, wait past `ExitUnlockAt`.
2. Call `ClearPosition` at time T3 > `ExitUnlockAt`.
3. Immediately claim rewards.
4. Assert that bonus for the interval `[ExitUnlockAt, T3]` is **zero**.

---

### Proof of Concept

**Prerequisites:**
- Tier with `BonusApy = 0.04`, `ExitDuration = 1 year`
- Position with 1 000 000 bond tokens, created at T0
- `ExitUnlockAt = T0 + 1 year` (exit triggered immediately at creation)

**Step 1 — Advance past ExitUnlockAt:**
At T2 = T0 + 1 year + 1 second, call `MsgClaimTierRewards`.
`processEventsAndClaimBonus` computes bonus for `[T0, T2]`, then `applyBonusAccrualCheckpoint` sets `LastBonusAccrual = ExitUnlockAt = T0 + 1 year`.

**Step 2 — Wait, then ClearPosition:**
At T3 = T0 + 2 years, call `MsgClearPosition`.
Inside `claimRewards`, `processEventsAndClaimBonus` runs with `segmentStart = T0 + 1 year`, `segmentEnd` capped at `ExitUnlockAt = T0 + 1 year` → zero bonus paid (correct).
`applyBonusAccrualCheckpoint` sets `LastBonusAccrual = ExitUnlockAt = T0 + 1 year`.
`ClearPosition` then resets `ExitUnlockAt = zero`.
Position saved: `LastBonusAccrual = T0 + 1 year`, `ExitUnlockAt = zero`.

**Step 3 — Claim after ClearPosition:**
At T4 = T0 + 2 years + 1 second, call `MsgClaimTierRewards`.
`processEventsAndClaimBonus` runs with `segmentStart = T0 + 1 year`, `segmentEnd = T4`.
No `ExitUnlockAt` cap applies.
**Bonus paid = 1 000 000 × 0.04 × (1 year) / 1 year = 40 000 tokens** — for a period when bonus was explicitly stopped.

**Expected:** 0 tokens for the `[T0+1yr, T3]` gap.
**Actual:** ~40 000 tokens extracted from the rewards pool. [1](#0-0) [6](#0-5)

### Citations

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

**File:** x/tieredrewards/keeper/bonus_rewards.go (L26-28)
```go
	if !pos.ExitUnlockAt.IsZero() && segmentEnd.After(pos.ExitUnlockAt) {
		segmentEnd = pos.ExitUnlockAt
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-217)
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
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-241)
```go
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
