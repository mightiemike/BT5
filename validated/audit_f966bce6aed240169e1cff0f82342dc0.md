### Title
Bonus Reward Truncation Permanently Consumes Accrual Window Without Paying Stakers - (`x/tieredrewards/keeper/claim_rewards.go`, `x/tieredrewards/keeper/bonus_rewards.go`)

---

### Summary

In `x/tieredrewards`, the bonus reward formula uses `TruncateInt()` to convert a `math.LegacyDec` result to an integer amount in the base denomination (`carson`). When the computed bonus rounds to zero — due to a short accrual window, a small position, or a low APY — `processEventsAndClaimBonus` still unconditionally advances `pos.LastBonusAccrual` to the current block time before checking whether the total bonus is zero. The time window that produced zero bonus is permanently consumed: the user cannot reclaim it, and the corresponding pool tokens are never paid out.

---

### Finding Description

`processEventsAndClaimBonus` in `x/tieredrewards/keeper/claim_rewards.go` computes the total bonus by summing `computeSegmentBonus` calls over each bonded segment. The segment formula is:

```
shares × tokensPerShare × bonusApy × durationSeconds / SecondsPerYear
```

implemented as:

```go
return tokens.
    Mul(tier.BonusApy).
    MulInt64(durationSeconds).
    QuoInt64(types.SecondsPerYear).
    TruncateInt()
``` [1](#0-0) 

`TruncateInt()` discards the fractional part. For a position with `shares × tokensPerShare = 1e8 carson` (1 CRO), `bonusApy = 0.04`, and `durationSeconds = 1`, the result is `1e8 × 0.04 × 1 / 31,557,600 ≈ 0.127`, which truncates to **0**.

After the loop and final-segment computation, `applyBonusAccrualCheckpoint` is called **unconditionally** at line 215, advancing `pos.LastBonusAccrual` to the current block time:

```go
applyBonusAccrualCheckpoint(&pos.Position, blockTime)   // line 215 — always runs
pos.UpdateLastKnownBonded(bonded)

if totalBonus.IsZero() {                                 // line 219 — checked AFTER checkpoint
    return sdk.NewCoins(), nil
}
``` [2](#0-1) 

`applyBonusAccrualCheckpoint` calls `pos.UpdateLastBonusAccrual(accrualEnd)`, permanently advancing the accrual cursor:

```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
    accrualEnd := blockTime
    if pos.CompletedExitLockDuration(blockTime) {
        accrualEnd = pos.ExitUnlockAt
    }
    pos.UpdateLastBonusAccrual(accrualEnd)
}
``` [3](#0-2) 

Because `LastBonusAccrual` is the `segmentStart` for the next claim, the zero-bonus window is gone — it will never be replayed.

The forced-claim paths that trigger this without user intent are: `MsgTierUndelegate`, `MsgTierRedelegate`, `MsgAddToTierPosition`, `MsgClearPosition`, and `MsgExitTierWithDelegation` — all of which call `claimRewards` before mutating the position. [4](#0-3) 

---

### Impact Explanation

Users with small positions (near `MinLockAmount`) or who perform two protocol operations in rapid succession (e.g., redelegate then immediately redelegate again) lose the bonus accrued during the short inter-operation window. The corresponding `carson` tokens remain in the `RewardsPoolName` module account but are not attributable to any position's future claim — they accumulate as permanently unclaimable dust. Over many positions and operations, the aggregate loss can be material.

---

### Likelihood Explanation

Any delegator who performs two tier-mutating operations within a short interval (seconds to a few minutes, depending on position size and APY) will silently lose bonus for that window. This is a normal usage pattern (e.g., redelegate to rebalance, then redelegate again after a validator change). No adversarial setup is required; the loss is triggered by ordinary `MsgTierRedelegate` or `MsgAddToTierPosition` transactions signed by the position owner.

---

### Recommendation

Accumulate the truncated remainder and carry it forward into the next accrual window, analogous to the `lastLQTYError` pattern suggested in the external report. Concretely, store a per-position `LastBonusAccrualRemainder math.LegacyDec` field. In `computeSegmentBonus` (or its caller), add the remainder to the numerator before dividing, then persist the new remainder:

```go
numerator := tokens.Mul(tier.BonusApy).MulInt64(durationSeconds).Add(pos.LastBonusAccrualRemainder)
result := numerator.QuoInt64(types.SecondsPerYear).TruncateInt()
pos.LastBonusAccrualRemainder = numerator.Sub(math.LegacyNewDecFromInt(result).MulInt64(types.SecondsPerYear))
```

Alternatively, only advance `LastBonusAccrual` when a non-zero bonus is actually paid, so that sub-threshold windows are re-included in the next claim.

---

### Proof of Concept

1. Create a tier position with `MinLockAmount` tokens (e.g., 1 CRO = `1e8 carson`) and `bonusApy = 0.04`.
2. Immediately redelegate the position to a different validator (forces `claimRewards` → `processEventsAndClaimBonus`). The elapsed time since position creation is ≤ 7 seconds, so `computeSegmentBonus` returns 0 for the segment.
3. Observe that `pos.LastBonusAccrual` has been advanced to the current block time despite zero bonus being paid.
4. The bonus for the initial lock-to-redelegate window is permanently lost; the rewards pool balance is unchanged but the user's entitlement to those tokens is gone.

Concretely, with `shares × tokensPerShare = 1e8`, `bonusApy = 0.04`, `durationSeconds = 7`:
`1e8 × 0.04 × 7 / 31,557,600 = 2.8e7 / 31,557,600 ≈ 0.887` → `TruncateInt()` → **0** [5](#0-4) [6](#0-5)

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

**File:** x/tieredrewards/keeper/bonus_rewards.go (L39-45)
```go
	tokens := pos.Delegation.Shares.Mul(tokensPerShare)

	return tokens.
		Mul(tier.BonusApy).
		MulInt64(durationSeconds).
		QuoInt64(types.SecondsPerYear).
		TruncateInt()
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L211-221)
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
```
