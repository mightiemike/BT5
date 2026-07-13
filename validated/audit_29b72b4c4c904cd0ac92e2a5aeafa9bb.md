### Title
`LastBonusAccrual` Checkpoint Advances Unconditionally Even When `computeSegmentBonus` Truncates to Zero, Causing Permanent Bonus Loss - (`x/tieredrewards/keeper/bonus_rewards.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` always advances the `LastBonusAccrual` checkpoint to `blockTime` via `applyBonusAccrualCheckpoint`, even when `computeSegmentBonus` returns zero due to integer truncation. This mirrors the root cause in the external report: a balance/checkpoint advances without the corresponding value being paid out, permanently consuming the accrued time period without distributing any bonus to the position owner.

---

### Finding Description

`computeSegmentBonus` computes bonus using the formula:

```
shares * tokensPerShare * bonusApy * durationSeconds / SecondsPerYear
```

and finalizes with `.TruncateInt()`:

```go
return tokens.
    Mul(tier.BonusApy).
    MulInt64(durationSeconds).
    QuoInt64(types.SecondsPerYear).
    TruncateInt()
``` [1](#0-0) 

This truncation returns `math.ZeroInt()` whenever the product is less than 1 token — which occurs for small positions, low APY tiers, or short claim intervals.

After the event loop and final segment computation, `processEventsAndClaimBonus` unconditionally calls `applyBonusAccrualCheckpoint`:

```go
applyBonusAccrualCheckpoint(&pos.Position, blockTime)
// Persist the bonded state so the next replay starts correctly.
pos.UpdateLastKnownBonded(bonded)

if totalBonus.IsZero() {
    return sdk.NewCoins(), nil
}
``` [2](#0-1) 

`applyBonusAccrualCheckpoint` sets `LastBonusAccrual` to `blockTime` (or `ExitUnlockAt` if the exit lock has elapsed):

```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
    accrualEnd := blockTime
    if pos.CompletedExitLockDuration(blockTime) {
        accrualEnd = pos.ExitUnlockAt
    }
    pos.UpdateLastBonusAccrual(accrualEnd)
}
``` [3](#0-2) 

The checkpoint is advanced **before** the zero-check at line 219. This means: even when `totalBonus == 0` due to truncation, `LastBonusAccrual` is written to state as `blockTime`. The time segment `[old LastBonusAccrual, blockTime]` is permanently consumed. The next claim starts from `blockTime`, and the truncated sub-unit bonus for the consumed period is irrecoverably lost.

**Concrete numeric example:**

- `shares = 1,000,000`, `tokensPerShare = 1.0`, `bonusApy = 0.05` (5%), `durationSeconds = 60` (1 minute)
- `bonus = 1,000,000 × 1.0 × 0.05 × 60 / 31,557,600 ≈ 0.095` → `TruncateInt() = 0`
- `LastBonusAccrual` is still advanced by 60 seconds
- The 0.095-token sub-unit is permanently lost

If the user had waited 11 minutes before claiming, the bonus would have been `≈ 1.05` → `TruncateInt() = 1` token paid out. By claiming every minute, the user loses all bonus for that position.

---

### Impact Explanation

Every call to `MsgClaimTierRewards` (or any reward-settling message: `MsgAddToTierPosition`, `MsgTierRedelegate`, `MsgTierUndelegate`, `MsgClearPosition`, `MsgExitTierWithDelegation`) that produces a zero-truncated bonus permanently consumes the accrual time window without paying any bonus. The position owner loses bonus rewards that would have been non-zero had they claimed less frequently. The loss is cumulative and proportional to claim frequency. For small positions or low-APY tiers, the minimum non-zero claim interval can be hours or days, meaning any user who claims more frequently than that threshold loses all bonus for those intervals.

---

### Likelihood Explanation

`MsgClaimTierRewards` is an unprivileged transaction callable by any position owner at any time. There is no enforced minimum interval between claims. On a chain with low gas costs or high transaction throughput, users may claim frequently (e.g., via automation or bots). Small positions (near `MinLockAmount`) are explicitly supported by the protocol. The truncation threshold is not negligible: for a 5% APY position, even a 1M-share position requires ~10 minutes of accrual to produce 1 token of bonus. Any claim interval shorter than that threshold silently loses the sub-unit bonus.

---

### Recommendation

Move `applyBonusAccrualCheckpoint` to execute only after confirming that a non-zero bonus was computed, or — more precisely — advance `LastBonusAccrual` only by the time corresponding to the actually-paid-out bonus (analogous to the external report's fix of advancing `lastBalance` by `deltaIndex.mulDown(totalShares)` rather than by `accrued`). The simplest safe fix is to guard the checkpoint:

```go
// Only advance the accrual checkpoint if bonus was actually paid out.
// If totalBonus truncated to zero, preserve LastBonusAccrual so the
// sub-unit time is not lost.
if !totalBonus.IsZero() {
    applyBonusAccrualCheckpoint(&pos.Position, blockTime)
}
pos.UpdateLastKnownBonded(bonded)
```

Note: `LastEventSeq` is still correctly advanced inside the event loop regardless, so event garbage-collection is unaffected by this change.

---

### Proof of Concept

1. Create a position with `MsgLockTier` using a small amount (e.g., `MinLockAmount`).
2. Advance block time by 30 seconds.
3. Call `MsgClaimTierRewards` — `computeSegmentBonus` returns 0 due to truncation, but `LastBonusAccrual` is advanced to `blockTime`.
4. Advance block time by another 30 seconds.
5. Call `MsgClaimTierRewards` again — again returns 0, `LastBonusAccrual` advanced again.
6. Repeat for N iterations totaling 10 minutes of elapsed time.
7. Observe: total bonus received = 0, even though a single claim after 10 minutes would have yielded a non-zero bonus.
8. The 10 minutes of accrual time is permanently lost across the N intermediate checkpoints.

The entry path is `MsgClaimTierRewards` → `claimRewardsAndUpdatesPositions` → `processEventsAndClaimBonus` → `applyBonusAccrualCheckpoint` (unconditional) at `claim_rewards.go:215`, with the truncation occurring at `bonus_rewards.go:45`. [4](#0-3) [5](#0-4)

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

**File:** x/tieredrewards/keeper/bonus_rewards.go (L25-46)
```go
func (k Keeper) computeSegmentBonus(pos types.PositionState, tier types.Tier, segmentStart, segmentEnd time.Time, tokensPerShare math.LegacyDec) math.Int {
	if !pos.ExitUnlockAt.IsZero() && segmentEnd.After(pos.ExitUnlockAt) {
		segmentEnd = pos.ExitUnlockAt
	}

	if !segmentEnd.After(segmentStart) {
		return math.ZeroInt()
	}

	durationSeconds := int64(segmentEnd.Sub(segmentStart) / time.Second)
	if durationSeconds <= 0 {
		return math.ZeroInt()
	}

	tokens := pos.Delegation.Shares.Mul(tokensPerShare)

	return tokens.
		Mul(tier.BonusApy).
		MulInt64(durationSeconds).
		QuoInt64(types.SecondsPerYear).
		TruncateInt()
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-221)
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

	if totalBonus.IsZero() {
		return sdk.NewCoins(), nil
	}
```
