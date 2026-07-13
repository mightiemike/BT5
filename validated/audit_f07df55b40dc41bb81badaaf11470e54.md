### Title
Bonus APY Applied at Claim-Time Rather Than Segment-Time Allows Retroactive Over-Claiming After Governance Tier Updates — (`x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` fetches the **current** `tier.BonusApy` once at claim time and applies it uniformly to every historical segment since the position's last claim. When governance raises a tier's `BonusApy`, any position holder who defers claiming receives the new, higher rate retroactively across all previously elapsed, unclaimed segments — draining the rewards pool beyond the protocol's intended rate.

---

### Finding Description

Inside `processEventsAndClaimBonus` the tier is resolved a single time at the top of the function:

```go
tier, err := k.getTier(ctx, pos.TierId)   // current BonusApy, not a historical snapshot
```

That same `tier` object — carrying the **current** `BonusApy` — is then passed into every call to `computeSegmentBonus`, both for historical validator-event segments and for the live trailing segment:

```go
for _, entry := range events {
    if bonded {
        bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
        ...
    }
}
// current trailing segment
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
``` [1](#0-0) 

`computeSegmentBonus` multiplies `tier.BonusApy` directly into the reward for every segment:

```go
return tokens.
    Mul(tier.BonusApy).
    MulInt64(durationSeconds).
    QuoInt64(types.SecondsPerYear).
    TruncateInt()
``` [2](#0-1) 

By contrast, `tokensPerShare` **is** snapshotted per segment via validator events (slashes, bond/unbond transitions). `BonusApy` is never snapshotted; no `VALIDATOR_EVENT_TYPE_APY_CHANGE` event type exists. The comment on `computeSegmentBonus` says "using a snapshot rate," but only `tokensPerShare` is actually snapshotted — `BonusApy` is not. [3](#0-2) 

`SetTier` is explicitly documented as the target of governance messages:

```go
// SetTier writes a tier after validation. Used by governance messages, genesis, and chain upgrades.
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
``` [4](#0-3) 

When a governance proposal to raise `BonusApy` executes at a known block height, every position holder who has not yet claimed rewards will, on their next `MsgClaimTierRewards`, receive the new higher APY applied to all segments elapsed since their last claim — including segments that accrued entirely before the governance change.

---

### Impact Explanation

The rewards pool (`types.RewardsPoolName`) is drained at a rate higher than governance intended. The over-payment per position equals:

```
(new_APY − old_APY) × principal × unclaimed_duration / SecondsPerYear
```

For a large position held for months without claiming, this can be a material fraction of the pool. The `sufficientBonusPoolBalance` check only prevents the pool from going negative; it does not prevent over-payment relative to the intended rate. [5](#0-4) [6](#0-5) 

---

### Likelihood Explanation

- Governance proposals to adjust tier parameters are a normal, expected protocol operation.
- The execution block height of a passing proposal is deterministic and publicly visible on-chain well before execution.
- No special privileges are required; any holder of a tiered position can exploit this by simply deferring `MsgClaimTierRewards` until after the governance execution block.
- The longer a position has gone without claiming, the larger the retroactive windfall.

---

### Recommendation

Snapshot `BonusApy` at each segment boundary. The cleanest approach mirrors how `tokensPerShare` is already handled: emit a new validator-event-style record whenever `SetTier` changes `BonusApy`, and store the historical APY alongside each event. `computeSegmentBonus` should then use the APY value that was active at `segmentStart`, not the current value fetched at claim time.

---

### Proof of Concept

1. Tier 1 has `BonusApy = 0.05` (5 %).
2. Alice locks 1,000,000 tokens in Tier 1 at block B₀ and does not claim.
3. Six months later, governance passes a proposal raising Tier 1 `BonusApy` to `0.10` (10 %). The proposal executes at block B₁.
4. Alice calls `MsgClaimTierRewards` at block B₁ + 1.
5. `processEventsAndClaimBonus` fetches `tier.BonusApy = 0.10` and applies it to the entire 6-month segment.
   - **Paid:** `1,000,000 × 0.10 × (6 months / 12 months) = 50,000 tokens`
   - **Intended:** `1,000,000 × 0.05 × (6 months / 12 months) = 25,000 tokens`
6. Alice over-claims **25,000 tokens** from the rewards pool with a single standard `MsgClaimTierRewards` transaction.

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L167-179)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-240)
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
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L23-26)
```go
// computeSegmentBonus computes bonus for a time segment using a snapshot rate.
// Formula: shares * tokensPerShare * tier.BonusApy * durationSeconds / SecondsPerYear
func (k Keeper) computeSegmentBonus(pos types.PositionState, tier types.Tier, segmentStart, segmentEnd time.Time, tokensPerShare math.LegacyDec) math.Int {
	if !pos.ExitUnlockAt.IsZero() && segmentEnd.After(pos.ExitUnlockAt) {
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

**File:** x/tieredrewards/keeper/bonus_rewards.go (L48-61)
```go
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
	if bonus.IsZero() {
		return nil
	}

	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetAllBalances(ctx, poolAddr)
	if !poolBalance.IsAllGTE(bonus) {
		return errorsmod.Wrapf(types.ErrInsufficientBonusPool,
			"bonus: %s, pool balance: %s",
			bonus.String(), poolBalance.String())
	}

	return nil
```

**File:** x/tieredrewards/keeper/tier.go (L26-28)
```go
// SetTier writes a tier after validation. Used by governance messages, genesis, and chain upgrades.
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
	if err := tier.Validate(); err != nil {
```
