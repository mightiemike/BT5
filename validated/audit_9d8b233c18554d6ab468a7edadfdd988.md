### Title
Frequent `MsgClaimTierRewards` Calls Suppress Bonus Accrual via Unconditional `LastBonusAccrual` Checkpoint Advancement - (File: x/tieredrewards/keeper/claim_rewards.go)

---

### Summary

In `processEventsAndClaimBonus`, `applyBonusAccrualCheckpoint` advances `pos.LastBonusAccrual` to `blockTime` unconditionally — even when `computeSegmentBonus` truncates to zero for short intervals. A position owner (or an authz grantee) who calls `MsgClaimTierRewards` at short intervals permanently discards elapsed time in zero-bonus slices, producing a persistently lower total bonus than a single claim over the same wall-clock period.

---

### Finding Description

`computeSegmentBonus` in `x/tieredrewards/keeper/bonus_rewards.go` computes:

```go
tokens.Mul(tier.BonusApy).MulInt64(durationSeconds).QuoInt64(types.SecondsPerYear).TruncateInt()
```

`TruncateInt()` discards the fractional part. For small positions or short intervals the result is zero. The threshold below which a single block interval (≈6 s) rounds to zero is:

```
shares × tokensPerShare × bonusApy × 6 / 31_557_600 < 1
→ shares × tokensPerShare < 31_557_600 / (bonusApy × 6)
```

For a 4 % APY tier that is ≈ 131 490 000 basecro ≈ **1.31 CRO**. Any position below that threshold earns zero bonus per block.

The critical defect is in `processEventsAndClaimBonus`:

```go
// line 211-212: bonus computed (may be zero)
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
totalBonus = totalBonus.Add(bonus)

// line 215: checkpoint ALWAYS advanced, regardless of totalBonus
applyBonusAccrualCheckpoint(&pos.Position, blockTime)
pos.UpdateLastKnownBonded(bonded)

// line 219: zero-bonus path returns early — but checkpoint is already written
if totalBonus.IsZero() {
    return sdk.NewCoins(), nil
}
```

`applyBonusAccrualCheckpoint` calls `pos.UpdateLastBonusAccrual(accrualEnd)`, writing the new timestamp to the position before the zero-check. The elapsed time since the previous `LastBonusAccrual` is permanently discarded; it cannot be recovered on the next claim because `segmentStart` will be the newly written `blockTime`, not the original start of the zero-bonus interval. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

Every call to `MsgClaimTierRewards` that produces a zero-bonus segment permanently advances `LastBonusAccrual` without paying anything. Splitting a 1-hour window into 600 × 6-second claims on a sub-threshold position yields **zero total bonus**, whereas a single claim after 1 hour yields the correct non-zero amount. The loss is permanent and compounds with claim frequency. The rewards pool retains the unspent bonus, but the position owner's entitled share is irrecoverably forfeited. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

The entry path is `MsgClaimTierRewards`, a normal signed Cosmos SDK transaction callable by the position owner or any authz grantee the owner has authorized. No privileged role is required. Automated reward-compounding bots or wallets that claim every block are a realistic trigger. The authz flow (explicitly listed as a valid entry path) means a third party granted `MsgClaimTierRewards` authorization can spam claims against the granting owner's positions. Positions near the governance-set `MinLockAmount` are most exposed; if `MinLockAmount` is set to a small value, many real positions fall below the per-block truncation threshold. [6](#0-5) 

---

### Recommendation

Move `applyBonusAccrualCheckpoint` (and `UpdateLastKnownBonded`) to execute **only when `totalBonus > 0`**, or alternatively accumulate elapsed time without advancing the checkpoint when the computed bonus is zero. A cleaner fix mirrors the Accountable Protocol mitigation: accumulate a sub-unit remainder in the position state so that fractional seconds are carried forward rather than discarded. At minimum, guard the checkpoint write:

```go
if !totalBonus.IsZero() {
    applyBonusAccrualCheckpoint(&pos.Position, blockTime)
    pos.UpdateLastKnownBonded(bonded)
}
```

This ensures `LastBonusAccrual` only advances when actual bonus is paid, preserving elapsed time for the next claim. [7](#0-6) 

---

### Proof of Concept

Scenario: position with 1 CRO locked (10^8 basecro), tier BonusApy = 0.04, no validator events, 6-second Cosmos blocks.

Per-block bonus = `10^8 × 1.0 × 0.04 × 6 / 31_557_600 ≈ 0.76` → `TruncateInt()` → **0**.

**Path A (600 claims, one per block over 1 hour):**
Each call: `computeSegmentBonus` returns 0, `applyBonusAccrualCheckpoint` advances `LastBonusAccrual` by 6 s. After 600 calls: total bonus = **0 basecro**.

**Path B (single claim after 1 hour):**
`durationSeconds = 3600`. Bonus = `10^8 × 0.04 × 3600 / 31_557_600 ≈ 456` → **456 basecro**.

The owner in Path A receives zero bonus for the entire hour despite holding an active delegated position, while Path B correctly pays 456 basecro. The difference is caused solely by the unconditional checkpoint advancement on each zero-bonus call. [8](#0-7) [9](#0-8)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-165)
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L206-221)
```go
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

**File:** x/tieredrewards/keeper/bonus_rewards.go (L34-45)
```go
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
```

**File:** x/tieredrewards/types/keys.go (L25-27)
```go
	// SecondsPerYear is 365.25 days, used to convert durations to years for bonus calculation.
	SecondsPerYear int64 = 31_557_600
)
```

**File:** x/tieredrewards/types/position.go (L76-78)
```go
func (p *Position) UpdateLastBonusAccrual(t time.Time) {
	p.LastBonusAccrual = t
}
```
