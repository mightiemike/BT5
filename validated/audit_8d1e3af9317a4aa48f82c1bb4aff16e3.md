### Title
Governance-Mutable `Tier.ExitDuration` Not Snapshotted in Position Retroactively Alters Exit Lock for Existing Positions — (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

The `Position` struct stores only a `TierId` reference to its tier, not a snapshot of `ExitDuration` at creation time. When `TriggerExitFromTier` is executed, it reads `ExitDuration` live from the global `Tier` store. A governance proposal that updates a tier's `ExitDuration` via `SetTier` will silently change the exit lock period for every existing position in that tier that has not yet triggered exit — extending or shortening the period users agreed to when they locked funds.

---

### Finding Description

When a user creates a position via `MsgLockTier` or `MsgCommitDelegationToTier`, the `Position` struct records `TierId` but does **not** snapshot `ExitDuration` (or `BonusApy`): [1](#0-0) 

Later, when the user calls `TriggerExitFromTier`, the handler fetches the tier live from the global store and computes `ExitUnlockAt` using the **current** `ExitDuration`, not the one in effect at lock time: [2](#0-1) 

The `SetTier` function, explicitly documented as the governance update path, performs no check for existing active positions before overwriting `ExitDuration`: [3](#0-2) 

By contrast, `deleteTier` does guard against active positions: [4](#0-3) 

The asymmetry is the root cause: deletion is blocked when positions exist, but mutation of the tier's parameters is not.

The same pattern applies to `BonusApy`: `computeSegmentBonus` reads `tier.BonusApy` from the live store at claim time, so a governance change to `BonusApy` retroactively reprices all pending unclaimed bonus rewards for every open position in the tier: [5](#0-4) 

---

### Impact Explanation

**`ExitDuration` increase**: A governance proposal raises `ExitDuration` for a tier. All users who locked into that tier and have not yet called `TriggerExitFromTier` will, when they do trigger, receive an `ExitUnlockAt` computed from the new (longer) duration. Their funds are locked for longer than the duration that was advertised and in effect when they committed. The corrupted on-chain value is `Position.ExitUnlockAt`, which is set to `blockTime + newExitDuration` instead of `blockTime + originalExitDuration`.

**`ExitDuration` decrease**: Conversely, a reduction shortens the lock, which may benefit users but violates the protocol invariant that the exit duration is fixed at lock time.

**`BonusApy` change**: Retroactively reprices all accrued-but-unclaimed bonus rewards for every open position in the tier. An increase drains the rewards pool faster than provisioned; a decrease reduces rewards users were entitled to for the period already elapsed.

---

### Likelihood Explanation

Governance proposals to adjust tier parameters (e.g., to respond to market conditions or fix misconfigured tiers) are a normal and expected operational action. The `SetTier` path is explicitly designed for governance use. Any token holder can submit such a proposal; if it passes, the effect on existing positions is immediate and silent. No exploit or key compromise is required.

---

### Recommendation

Snapshot the tier's `ExitDuration` (and `BonusApy`) into the `Position` struct at creation time, mirroring the pattern used by `supportRequiredPct`/`minAcceptQuorumPct` in the referenced Aragon report. For `ExitDuration`, store it in `Position` and use the stored value in `TriggerExit`. For `BonusApy`, store it in `Position` and use the stored value in `computeSegmentBonus`. Governance updates to a tier should only affect **new** positions created after the update, not existing ones.

---

### Proof of Concept

1. User A calls `MsgLockTier` for tier ID 1, which has `ExitDuration = 7 days`. `Position{TierId: 1, ExitTriggeredAt: zero, ExitUnlockAt: zero}` is stored — no `ExitDuration` snapshot. [6](#0-5) 

2. Governance passes a proposal calling `SetTier` with tier ID 1, `ExitDuration = 30 days`. The store is updated with no guard for active positions. [7](#0-6) 

3. User A calls `MsgTriggerExitFromTier`. The handler reads `tier.ExitDuration = 30 days` from the live store and sets `ExitUnlockAt = blockTime + 30 days`. [2](#0-1) 

4. User A's funds are locked for 30 days instead of the 7 days that were in effect when they locked. The `ExitUnlockAt` field in the position is corrupted relative to the user's original agreement. [8](#0-7)

### Citations

**File:** x/tieredrewards/types/position.go (L15-27)
```go
func NewPosition(id uint64, owner string, tierId uint32, delegatorAddress string, createdAtHeight, lastEventSeq uint64, lastBonusAccrual time.Time, lastKnownBonded bool, createdAtTime time.Time) Position {
	return Position{
		Id:               id,
		Owner:            owner,
		TierId:           tierId,
		DelegatorAddress: delegatorAddress,
		CreatedAtHeight:  createdAtHeight,
		CreatedAtTime:    createdAtTime,
		LastEventSeq:     lastEventSeq,
		LastBonusAccrual: lastBonusAccrual,
		LastKnownBonded:  lastKnownBonded,
	}
}
```

**File:** x/tieredrewards/types/position.go (L71-74)
```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
	p.ExitTriggeredAt = blockTime
	p.ExitUnlockAt = blockTime.Add(duration)
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L361-368)
```go
	tier, err := ms.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)

```

**File:** x/tieredrewards/keeper/tier.go (L26-35)
```go
// SetTier writes a tier after validation. Used by governance messages, genesis, and chain upgrades.
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
	if err := tier.Validate(); err != nil {
		return err
	}
	if err := k.Tiers.Set(ctx, tier.Id, tier); err != nil {
		return errorsmod.Wrapf(err, "%s (tier id %d)", types.ErrTierStore.Error(), tier.Id)
	}
	return nil
}
```

**File:** x/tieredrewards/keeper/tier.go (L37-52)
```go
func (k Keeper) deleteTier(ctx context.Context, tierId uint32) error {
	hasPositions, err := k.hasPositionsForTier(ctx, tierId)
	if err != nil {
		return err
	}
	if hasPositions {
		return types.ErrTierHasActivePositions
	}
	if err := k.Tiers.Remove(ctx, tierId); err != nil {
		if stderrors.Is(err, collections.ErrNotFound) {
			return errorsmod.Wrapf(types.ErrTierNotFound, "tier id %d", tierId)
		}
		return errorsmod.Wrapf(err, "%s (tier id %d)", types.ErrTierStore.Error(), tierId)
	}
	return nil
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

**File:** x/tieredrewards/keeper/position.go (L34-72)
```go
func (k Keeper) createDelegatedPosition(
	ctx context.Context,
	owner string,
	tier types.Tier,
	valAddr sdk.ValAddress,
	delAddr sdk.AccAddress,
	triggerExitImmediately bool,
) (types.Position, error) {
	id, err := k.NextPositionId.Next(ctx)
	if err != nil {
		return types.Position{}, err
	}

	lastEventSeq, err := k.getValidatorEventLatestSeq(ctx, valAddr)
	if err != nil {
		return types.Position{}, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	blockTime := sdkCtx.BlockTime()
	blockHeight := uint64(sdkCtx.BlockHeight())

	pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, true, blockTime)

	ownerAddr, err := sdk.AccAddressFromBech32(owner)
	if err != nil {
		return types.Position{}, err
	}

	if err := k.routeBaseRewardsToOwner(ctx, delAddr, ownerAddr); err != nil {
		return types.Position{}, err
	}

	if triggerExitImmediately {
		pos.TriggerExit(blockTime, tier.ExitDuration)
	}

	return pos, nil
}
```
