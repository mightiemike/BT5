### Title
`processEventsAndClaimBonus` Applies Current `BonusApy` Retroactively to All Historical Reward Segments After Governance Tier Update - (File: `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` fetches the current `tier.BonusApy` once at claim time and applies it uniformly to every historical reward segment since `pos.LastBonusAccrual`. When governance changes `BonusApy` via `MsgUpdateTier`, all unclaimed historical segments are retroactively recalculated at the new APY rather than the APY that was in effect when those segments accrued. This causes underpayment to users (if APY decreased) or overpayment from the bonus pool (if APY increased).

---

### Finding Description

In `processEventsAndClaimBonus`, the tier is fetched exactly once at the start of the function:

```go
tier, err := k.getTier(ctx, pos.TierId)
```

That single `tier` value — including its `BonusApy` — is then passed to `computeSegmentBonus` for every historical segment in the event replay loop and for the final open segment:

```go
// inside the event loop
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)

// final open segment
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
```

Inside `computeSegmentBonus`, `tier.BonusApy` is multiplied against the full segment duration:

```go
return tokens.
    Mul(tier.BonusApy).
    MulInt64(durationSeconds).
    QuoInt64(types.SecondsPerYear).
    TruncateInt()
```

The design correctly snapshots `TokensPerShare` at each validator event (stored in `ValidatorEvent.TokensPerShare`) to preserve historical accuracy for slash-adjusted token values. However, no equivalent snapshotting exists for `BonusApy`. If governance passes `MsgUpdateTier` and changes `BonusApy` between a position's `LastBonusAccrual` and the next `MsgClaimTierRewards`, the entire unclaimed period — including the portion that accrued before the governance change — is recalculated at the new APY.

The broken invariant: bonus rewards for a time segment must be calculated using the `BonusApy` that was in effect during that segment, not the APY at claim time.

---

### Impact Explanation

**Underpayment (APY decrease via governance):** A user who held a position earning 10% APY for six months before governance reduced it to 5% would receive only half the bonus they legitimately earned. The delta is a direct, quantifiable loss of earned rewards from the user's perspective.

**Overpayment (APY increase via governance):** The bonus pool (`RewardsPoolName`) is drained faster than the protocol intended. This can trigger `ErrInsufficientBonusPool` for other claimants, causing their entire `MsgClaimTierRewards` transaction to revert (including base reward withdrawal), effectively blocking reward claims for unrelated users.

The corrupted value is the `bonusCoins` amount sent via `k.bankKeeper.SendCoinsFromModuleToAccount` from the module's `RewardsPoolName` account to the position owner.

---

### Likelihood Explanation

Moderate. The trigger requires a governance `MsgUpdateTier` that modifies `BonusApy`, which is a standard, supported governance operation. Any position holder who has not claimed since the governance change is affected. The longer the gap between claims and the larger the position, the greater the discrepancy. No special attacker capability is required beyond holding an active position and submitting a normal `MsgClaimTierRewards` transaction.

---

### Recommendation

Snapshot `BonusApy` at each validator event (analogous to how `TokensPerShare` is already snapshotted in `ValidatorEvent`), so that `processEventsAndClaimBonus` can apply the historically correct APY to each segment. Alternatively, record a dedicated `APY_CHANGE` event type in the validator event log whenever governance updates a tier, allowing the replay loop to switch APY at the correct boundary.

---

### Proof of Concept

1. User creates a position in tier 1 (`BonusApy = 10%`) and delegates 1,000 tokens to a bonded validator. `LastBonusAccrual` is set to `T0`.
2. Six months pass. No claims are made. The position accrues approximately 50 tokens in bonus rewards at 10% APY.
3. Governance passes `MsgUpdateTier` reducing tier 1's `BonusApy` to 5%. `LastBonusAccrual` is still `T0`.
4. User submits `MsgClaimTierRewards`.
5. `processEventsAndClaimBonus` fetches the tier and reads `BonusApy = 5%`. It applies this rate to the entire six-month segment `[T0, now]`.
6. User receives approximately 25 tokens instead of 50 — losing 25 tokens of earned rewards with no recourse.

The root cause is structurally identical to the external report: a value that should reflect the state at a specific historical point in time instead reflects the current state, causing incorrect reward accounting. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L167-170)
```go
	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-179)
```go
	for _, entry := range events {
		evt := entry.Event

		if bonded {
			// Compute bonus for the bonded segment [segmentStart, eventTime]
			// using the snapshot rate at the event.
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L201-212)
```go
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

**File:** x/tieredrewards/types/types.pb.go (L73-74)
```go
	// bonus_apy is the fixed bonus APY (per year) for this tier, e.g. "0.04" = 4%.
	BonusApy cosmossdk_io_math.LegacyDec `protobuf:"bytes,3,opt,name=bonus_apy,json=bonusApy,proto3,customtype=cosmossdk.io/math.LegacyDec" json:"bonus_apy"`
```

**File:** x/tieredrewards/types/types.pb.go (L399-402)
```go
	// tokens_per_share is the validator's token-per-share rate at event time,
	// used to snapshot the token value for bonus calculation.
	TokensPerShare cosmossdk_io_math.LegacyDec `protobuf:"bytes,4,opt,name=tokens_per_share,json=tokensPerShare,proto3,customtype=cosmossdk.io/math.LegacyDec" json:"tokens_per_share"`
	// reference_count tracks how many positions still need to process this event.
```
