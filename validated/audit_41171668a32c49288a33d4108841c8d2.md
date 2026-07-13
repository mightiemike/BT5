### Title
Bonus Reward Loss Due to Rounding-to-Zero in `computeSegmentBonus` with Unconditional Accrual Checkpoint Advancement — (`x/tieredrewards/keeper/bonus_rewards.go`)

---

### Summary

The `computeSegmentBonus` function in `x/tieredrewards/keeper/bonus_rewards.go` truncates the bonus calculation to an integer. When `MsgClaimTierRewards` (or any reward-settling message) is submitted with a short elapsed time since the last claim, the computed bonus rounds to zero. Critically, `applyBonusAccrualCheckpoint` is called **unconditionally** in `processEventsAndClaimBonus` regardless of whether any bonus was paid, permanently advancing `pos.LastBonusAccrual` and consuming the time window without compensating the owner. The accrued-but-unissued bonus is silently discarded.

---

### Finding Description

**Root cause — integer truncation in `computeSegmentBonus`:** [1](#0-0) 

```go
return tokens.
    Mul(tier.BonusApy).
    MulInt64(durationSeconds).
    QuoInt64(types.SecondsPerYear).
    TruncateInt()
```

`SecondsPerYear` is 31,557,600. For the result to be non-zero, the numerator `tokens × bonusApy × durationSeconds` must be ≥ 31,557,600. For a position holding 10,000 tokens with a 4% APY:

```
10,000 × 0.04 × durationSeconds ≥ 31,557,600
durationSeconds ≥ 78,894  (~22 hours)
```

Any claim made more frequently than once every ~22 hours for this position size yields a zero bonus.

**Root cause — unconditional checkpoint advancement:** [2](#0-1) 

```go
applyBonusAccrualCheckpoint(&pos.Position, blockTime)
// Persist the bonded state so the next replay starts correctly.
pos.UpdateLastKnownBonded(bonded)
```

`applyBonusAccrualCheckpoint` is called at line 215 unconditionally — even when `totalBonus.IsZero()` (checked at line 219). The function advances `pos.LastBonusAccrual` to `blockTime`: [3](#0-2) 

```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
    accrualEnd := blockTime
    if pos.CompletedExitLockDuration(blockTime) {
        accrualEnd = pos.ExitUnlockAt
    }
    pos.UpdateLastBonusAccrual(accrualEnd)
}
```

The updated position is then persisted via `setPosition` in every calling path (`claimRewardsAndUpdatesPositions` line 129, `claimRewards` line 102 → callers persist). The time window `[old LastBonusAccrual, blockTime)` is permanently consumed with zero bonus paid.

**Entry paths that trigger this:**

All of the following owner-signed messages call `claimRewards` or `claimRewardsAndUpdatesPositions` before their mutation, each of which calls `processEventsAndClaimBonus`: [4](#0-3) 

- `MsgClaimTierRewards` (line 448)
- `MsgTierUndelegate` (line 166)
- `MsgTierRedelegate` (line 229)
- `MsgAddToTierPosition` (line 314)
- `MsgClearPosition` (line 406)
- `MsgExitTierWithDelegation` (line 532)

Additionally, `claimRewardsAndUpdateTierPositions` (called from `msg_server_auth.go`) iterates **all positions in a tier** and claims rewards for each one, meaning a governance/admin-triggered tier update can cause this rounding loss across every position in the tier simultaneously. [5](#0-4) 

---

### Impact Explanation

For each claim where `durationSeconds` is below the rounding threshold, the bonus for that entire time window is permanently lost from the owner's perspective — the pool retains the tokens but the position's accrual pointer has already moved past the window. The loss compounds: a position owner whose bot submits `MsgClaimTierRewards` every block (≈5 s on CRO chain) loses **all** bonus rewards indefinitely, since every segment rounds to zero. The larger the total bonded supply relative to the position size, the worse the rounding threshold becomes.

---

### Likelihood Explanation

Any position owner running an automated claiming bot (a common pattern for maximizing yield) will trigger this. The threshold is not exotic: a 10,000-token position at 4% APY requires claims no more frequently than once every ~22 hours to avoid zero-rounding. Blocks on Cronos POS are ~5 seconds, so a per-block bot loses 100% of bonus rewards. The governance path via `claimRewardsAndUpdateTierPositions` can affect all positions in a tier at once.

---

### Recommendation

Move `applyBonusAccrualCheckpoint` inside the `if !totalBonus.IsZero()` branch, **or** only advance `LastBonusAccrual` by the portion of time that actually contributed to a non-zero bonus (i.e., skip the checkpoint when the computed bonus truncates to zero). A cleaner fix is to accumulate bonus in a higher-precision per-position running total (a `Dec` accumulator) and only truncate when actually transferring coins, analogous to Cosmos SDK's `DecCoins` pattern used in `x/distribution`.

---

### Proof of Concept

1. Create a tier with `BonusApy = 0.04` (4%).
2. Lock 10,000 tokens into a position on a bonded validator.
3. Submit `MsgClaimTierRewards` every block (every 5 seconds).
4. Each call: `durationSeconds = 5`, `tokens = 10,000`, `bonus = 10,000 × 0.04 × 5 / 31,557,600 = 0.006...` → `TruncateInt()` → **0**.
5. `applyBonusAccrualCheckpoint` advances `LastBonusAccrual` by 5 seconds unconditionally.
6. After 24 hours (17,280 blocks), the owner has received **0** bonus despite being entitled to `10,000 × 0.04 × 86,400 / 31,557,600 ≈ 1.09` tokens.
7. All 1.09 tokens remain in the rewards pool, permanently inaccessible to this position. [6](#0-5) [7](#0-6)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L50-79)
```go
func (k Keeper) claimRewardsAndUpdateTierPositions(ctx context.Context, tierId uint32) error {
	ids, err := k.getPositionsIdsByTier(ctx, tierId)
	if err != nil {
		return err
	}
	if len(ids) == 0 {
		return nil
	}

	for _, id := range ids {
		pos, err := k.getPositionState(ctx, id)
		if err != nil {
			return err
		}
		if !pos.IsDelegated() {
			continue
		}

		if _, err := k.claimBaseRewards(ctx, pos); err != nil {
			return err
		}
		if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
			return err
		}
		if err := k.setPosition(ctx, pos.Position, nil); err != nil {
			return err
		}
	}

	return nil
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

**File:** x/tieredrewards/keeper/msg_server.go (L429-468)
```go
func (ms msgServer) ClaimTierRewards(ctx context.Context, msg *types.MsgClaimTierRewards) (*types.MsgClaimTierRewardsResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	positions := make([]types.PositionState, 0, len(msg.PositionIds))
	for _, posId := range msg.PositionIds {
		pos, err := ms.getPositionState(ctx, posId)
		if err != nil {
			return nil, err
		}

		if err := ms.validateClaimRewards(pos.Position, msg.Owner); err != nil {
			return nil, err
		}

		positions = append(positions, pos)
	}

	totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventTierRewardsClaimed{
		Owner:        msg.Owner,
		PositionIds:  msg.PositionIds,
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
	}); err != nil {
		return nil, err
	}

	return &types.MsgClaimTierRewardsResponse{
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
		PositionIds:  msg.PositionIds,
	}, nil
}
```
