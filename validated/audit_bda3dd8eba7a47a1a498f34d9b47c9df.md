### Title
Bonus Reward Precision Loss: `LastBonusAccrual` Advances Even When `computeSegmentBonus` Truncates to Zero - (`x/tieredrewards/keeper/bonus_rewards.go`)

---

### Summary

`computeSegmentBonus` in `x/tieredrewards` uses integer truncation (`TruncateInt()`) on the formula `shares × tokensPerShare × bonusApy × durationSeconds / SecondsPerYear`. For short accrual segments or small positions, this truncates to zero. Critically, `applyBonusAccrualCheckpoint` advances `pos.LastBonusAccrual` to `blockTime` **unconditionally**, regardless of whether any bonus was computed. The time window is permanently consumed and the bonus for that period is irrecoverably lost.

---

### Finding Description

`computeSegmentBonus` computes:

```go
return tokens.
    Mul(tier.BonusApy).
    MulInt64(durationSeconds).
    QuoInt64(types.SecondsPerYear).
    TruncateInt()
``` [1](#0-0) 

where `SecondsPerYear = 31_557_600`. [2](#0-1) 

For a position at the minimum lock amount of `1,000,000 basecro` with `bonusApy = 0.04` (4%):

```
tokens × 0.04 × durationSeconds / 31_557_600
= 1_000_000 × 0.04 × durationSeconds / 31_557_600
= 40_000 × durationSeconds / 31_557_600
```

This truncates to **0** for any `durationSeconds < 789` (~13 minutes).

After `processEventsAndClaimBonus` runs, `applyBonusAccrualCheckpoint` is called **unconditionally** at line 215, before the `totalBonus.IsZero()` check:

```go
applyBonusAccrualCheckpoint(&pos.Position, blockTime)
// Persist the bonded state so the next replay starts correctly.
pos.UpdateLastKnownBonded(bonded)

if totalBonus.IsZero() {
    return sdk.NewCoins(), nil
}
``` [3](#0-2) 

`applyBonusAccrualCheckpoint` sets `pos.LastBonusAccrual = blockTime`:

```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
    accrualEnd := blockTime
    ...
    pos.UpdateLastBonusAccrual(accrualEnd)
}
``` [4](#0-3) 

The next call to `processEventsAndClaimBonus` will use the advanced `LastBonusAccrual` as `segmentStart`, so the time window where bonus was zero is permanently discarded and cannot be recovered.

This affects every code path that calls `claimRewards` or `processEventsAndClaimBonus`:
- `MsgClaimTierRewards`
- `MsgAddToTierPosition`
- `MsgTierRedelegate`
- `MsgTierUndelegate`
- `MsgClearPosition`
- `MsgExitTierWithDelegation` [5](#0-4) 

---

### Impact Explanation

Position owners lose bonus rewards they are entitled to whenever any reward-settling operation is performed within a short interval. The loss is permanent because `LastBonusAccrual` advances unconditionally.

For a minimum-size position (`1,000,000 basecro`, 4% APY):
- Any operation within ~13 minutes of the last claim loses 100% of the bonus for that interval.
- A user who claims every 10 minutes loses all bonus rewards entirely.

For a `5,000,000 basecro` position at 2% APY (tier 2 from integration config):
- Minimum interval for non-zero bonus: `5_000_000 × 0.02 × t / 31_557_600 ≥ 1` → `t ≥ 316 seconds` (~5 minutes). [6](#0-5) 

The loss compounds over time. A user performing normal operations (redelegating, adding to position) at intervals shorter than the threshold permanently forfeits all bonus for those intervals.

---

### Likelihood Explanation

Any unprivileged position owner can trigger this by calling `MsgClaimTierRewards` or any other reward-settling message. The trigger is a standard Cosmos SDK transaction requiring no special permissions. Normal usage patterns (e.g., redelegating, adding tokens to a position) naturally trigger reward settlement and can cause this loss without the user being aware. The `MinLockAmount` of `1,000,000 basecro` is the minimum position size, making the ~13-minute threshold the worst-case scenario that applies to all positions at the minimum size.

---

### Recommendation

Defer advancing `LastBonusAccrual` only when a non-zero bonus is actually computed, or accumulate sub-integer bonus in a persistent `pendingBonus` field on the position and only advance the checkpoint when the accumulated value reaches at least 1 unit. Alternatively, scale intermediate calculations to a higher precision before truncating, similar to the recommendation in M-18 to scale up to 18 decimals.

---

### Proof of Concept

Given:
- `MinLockAmount = 1_000_000 basecro`
- `bonusApy = 0.04` (4%)
- `tokensPerShare = 1.0` (no slashing)
- `SecondsPerYear = 31_557_600`

**Step 1:** User creates a position with `1_000_000 basecro` at T=0. `LastBonusAccrual = T0`.

**Step 2:** User calls `MsgClaimTierRewards` at T=500s (8.3 minutes).

`computeSegmentBonus` computes:
```
1_000_000 × 1.0 × 0.04 × 500 / 31_557_600
= 20_000 / 31_557_600
≈ 0.000634
→ TruncateInt() = 0
```

`applyBonusAccrualCheckpoint` sets `LastBonusAccrual = T0 + 500s`.

**Step 3:** The 500-second window is permanently lost. The next claim starts from `T0 + 500s`.

**Step 4:** If the user repeats this every 500 seconds, they earn **zero bonus** indefinitely despite holding a valid delegated position in a tier with 4% APY.

The correct annual bonus for a `1_000_000 basecro` position at 4% APY is `40_000 basecro/year`. With claims every 500 seconds, the user receives `0 basecro/year` — a 100% loss of entitled bonus rewards.

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

**File:** x/tieredrewards/keeper/bonus_rewards.go (L41-45)
```go
	return tokens.
		Mul(tier.BonusApy).
		MulInt64(durationSeconds).
		QuoInt64(types.SecondsPerYear).
		TruncateInt()
```

**File:** x/tieredrewards/types/keys.go (L25-26)
```go
	// SecondsPerYear is 365.25 days, used to convert durations to years for bonus calculation.
	SecondsPerYear int64 = 31_557_600
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L87-103)
```go
func (k Keeper) claimRewards(ctx context.Context, pos types.PositionState) (types.PositionState, sdk.Coins, sdk.Coins, error) {
	if !pos.IsDelegated() {
		return pos, sdk.NewCoins(), sdk.NewCoins(), nil
	}

	base, err := k.claimBaseRewards(ctx, pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	return pos, base, bonus, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-221)
```go
	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)

	if totalBonus.IsZero() {
		return sdk.NewCoins(), nil
	}
```

**File:** integration_tests/configs/tieredrewards.jsonnet (L52-67)
```text
          tiers: [
            {
              id: 1,
              exit_duration: '5s',
              bonus_apy: '0.040000000000000000',
              min_lock_amount: '1000000',
              close_only: false,
            },
            {
              id: 2,
              exit_duration: '60s',
              bonus_apy: '0.020000000000000000',
              min_lock_amount: '5000000',
              close_only: false,
            },
          ],
```
