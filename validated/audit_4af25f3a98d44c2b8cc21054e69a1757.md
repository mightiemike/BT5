### Title
Stale Global `BonusApy` Used for Historical Reward Segments After Governance Tier Update — (`File: x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` fetches the **current** live tier (including its current `BonusApy`) and applies it uniformly to every historical time segment since the position's last claim. If governance has changed `BonusApy` between the last claim and the current claim, all pre-change segments are retroactively repriced at the new rate, producing incorrect bonus payouts.

---

### Finding Description

In `processEventsAndClaimBonus`, the tier is loaded once from live state at the top of the function:

```go
tier, err := k.getTier(ctx, pos.TierId)
``` [1](#0-0) 

`getTier` reads directly from the live `k.Tiers` collection — the current on-chain value:

```go
func (k Keeper) getTier(ctx context.Context, id uint32) (types.Tier, error) {
    tier, err := k.Tiers.Get(ctx, id)
``` [2](#0-1) 

This single `tier` object is then passed to `computeSegmentBonus` for **every** historical segment in the event loop, including segments that predate any governance change:

```go
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
``` [3](#0-2) 

And for the final open segment:

```go
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
``` [4](#0-3) 

Inside `computeSegmentBonus`, `tier.BonusApy` is the multiplier applied to every segment:

```go
return tokens.
    Mul(tier.BonusApy).
    MulInt64(durationSeconds).
    QuoInt64(types.SecondsPerYear).
    TruncateInt()
``` [5](#0-4) 

Governance can update `BonusApy` at any time via `SetTier`, which is explicitly documented as the governance write path:

```go
// SetTier writes a tier after validation. Used by governance messages, genesis, and chain upgrades.
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
``` [6](#0-5) 

No snapshot of the historical `BonusApy` is stored per-position or per-segment. The `Position` struct stores `TierId` (a reference), not the rate value that was in effect at lock time or at each segment boundary.

---

### Impact Explanation

**Overpayment scenario (governance raises `BonusApy`):** A user who has not claimed since before the governance change will have all their historical segments repriced at the higher new rate. They receive more bonus tokens from the `RewardsPoolName` module account than they earned, draining the pool faster than the protocol intends and shortchanging future claimants.

**Underpayment scenario (governance lowers `BonusApy`):** The same user's historical segments are repriced at the lower new rate. They receive fewer tokens than they earned during the higher-rate period, constituting a retroactive reduction of accrued rewards.

Both directions corrupt the invariant that bonus rewards are computed at the rate in effect during each accrual period. The corrupted value is the `bonusCoins` amount sent from `types.RewardsPoolName` to the position owner. [7](#0-6) 

**Impact: Medium** — direct incorrect token transfer to/from the rewards pool.

---

### Likelihood Explanation

**Likelihood: Low** — requires a governance proposal to change `BonusApy` on a tier that has active, unclaimed positions. Governance changes are infrequent but are a supported, documented production path. Any position that has not claimed since before the governance block is affected. The longer the gap between claims, the larger the mispriced segment.

---

### Recommendation

Before applying `computeSegmentBonus` to a segment, the `BonusApy` in effect during that segment must be used, not the current live value. Two approaches:

1. **Snapshot `BonusApy` into the position at lock time** and store it in the `Position` proto. Use the stored value for all bonus calculations for that position's lifetime.
2. **Emit a tier-update event** (analogous to `ValidatorEvent`) whenever `BonusApy` changes, and replay it during `processEventsAndClaimBonus` to switch rates at the correct block time boundary — the same mechanism already used for validator bond/unbond/slash transitions.

Option 2 is more precise but more complex. Option 1 is simpler and sufficient if the intent is that a position's rate is fixed at lock time.

---

### Proof of Concept

1. Governance sets Tier 1 `BonusApy = 4%` at genesis.
2. Alice calls `MsgLockTier` for Tier 1 at `T=0`. Her position accrues bonus at 4% APY.
3. 180 days pass. Alice does not claim.
4. Governance passes a proposal calling `SetTier` with `BonusApy = 8%` at `T=180d`.
5. Alice calls `MsgClaimTierRewards` at `T=181d`.
6. `processEventsAndClaimBonus` loads the current tier with `BonusApy = 8%`.
7. `computeSegmentBonus` is called for the segment `[T=0, T=181d]` using `BonusApy = 8%`.
8. Alice receives ~2× the bonus she earned for the first 180 days, draining the rewards pool.

The entry path is a standard `MsgClaimTierRewards` transaction signed by any position owner — no privilege required. [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-170)
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L178-179)
```go
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L211-212)
```go
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-241)
```go
	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

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

**File:** x/tieredrewards/keeper/tier.go (L15-17)
```go
func (k Keeper) getTier(ctx context.Context, id uint32) (types.Tier, error) {
	tier, err := k.Tiers.Get(ctx, id)
	if err != nil {
```

**File:** x/tieredrewards/keeper/tier.go (L26-28)
```go
// SetTier writes a tier after validation. Used by governance messages, genesis, and chain upgrades.
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
	if err := tier.Validate(); err != nil {
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L41-45)
```go
	return tokens.
		Mul(tier.BonusApy).
		MulInt64(durationSeconds).
		QuoInt64(types.SecondsPerYear).
		TruncateInt()
```
