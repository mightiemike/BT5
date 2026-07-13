### Title
Governance Modification of Tier `ExitDuration` Retroactively Extends Lock Period for Existing Positions — (`x/tieredrewards/keeper/tier.go`, `x/tieredrewards/types/position.go`)

---

### Summary

`SetTier` permits governance to overwrite any field of a `Tier`—including `ExitDuration`—without checking whether active positions exist for that tier. Because `ExitUnlockAt` is computed from the **current** tier's `ExitDuration` at the moment a user calls `MsgTriggerExitFromTier`, increasing `ExitDuration` after positions are created silently extends the lock period beyond what users agreed to when they locked their funds.

---

### Finding Description

`SetTier` in `x/tieredrewards/keeper/tier.go` writes the new tier unconditionally:

```go
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
    if err := tier.Validate(); err != nil {
        return err
    }
    if err := k.Tiers.Set(ctx, tier.Id, tier); err != nil { ... }
    return nil
}
``` [1](#0-0) 

There is no guard against active positions. Compare this with `deleteTier`, which explicitly blocks removal when positions exist:

```go
func (k Keeper) deleteTier(ctx context.Context, tierId uint32) error {
    hasPositions, err := k.hasPositionsForTier(ctx, tierId)
    ...
    if hasPositions {
        return types.ErrTierHasActivePositions
    }
``` [2](#0-1) 

When a user triggers exit, `TriggerExit` is called with the **live** tier's `ExitDuration`:

```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
    p.ExitTriggeredAt = blockTime
    p.ExitUnlockAt = blockTime.Add(duration)
}
``` [3](#0-2) 

The `Position` struct stores only `TierId`—not the `ExitDuration` that was in effect at creation time:

```go
pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), ...)
``` [4](#0-3) 

`NewPosition` accepts no `exitDuration` parameter, so no snapshot of the original lock term is persisted. [5](#0-4) 

---

### Impact Explanation

A user who locks CRO into a tier advertised with a 1-year `ExitDuration` has a reasonable expectation that triggering exit will start a 1-year countdown. If governance passes a proposal that raises `ExitDuration` to 5 years before the user calls `MsgTriggerExitFromTier`, the user's `ExitUnlockAt` is set 5 years in the future. The user's funds remain inaccessible for 4 additional years beyond what was promised. This is a direct loss of liquidity and constitutes a broken protocol invariant: the lock term agreed to at position creation is not honored.

---

### Likelihood Explanation

`SetTier` is the standard governance path for updating tier configuration (e.g., adjusting `BonusApy` or `MinLockAmount` in response to market conditions). A governance proposal to change `ExitDuration` is a routine, on-chain, permissionless-to-propose action. Given that tiers are expected to evolve over the protocol's lifetime, and that no code prevents modifying `ExitDuration` while positions are open, this scenario is realistically reachable without any privileged key or social engineering.

---

### Recommendation

Store the `ExitDuration` in effect at position creation inside the `Position` struct (analogous to the external report's recommendation to store `loanTerm` in the lien). When `TriggerExit` is called, use the stored value instead of the live tier value:

```diff
// In NewPosition / createDelegatedPosition:
- pos := types.NewPosition(id, owner, tier.Id, ...)
+ pos := types.NewPosition(id, owner, tier.Id, tier.ExitDuration, ...)

// In TriggerExit call site:
- pos.TriggerExit(blockTime, tier.ExitDuration)
+ pos.TriggerExit(blockTime, pos.LockedExitDuration)
```

Alternatively, `SetTier` should reject changes to `ExitDuration` when `PositionCountByTier` for that tier is non-zero, mirroring the guard already present in `deleteTier`.

---

### Proof of Concept

1. Governance creates Tier 1 with `ExitDuration = 1 year`.
2. Alice calls `MsgLockTier` and locks 10,000 CRO into Tier 1, expecting a 1-year exit window.
3. Governance submits and passes a proposal calling `SetTier` with Tier 1's `ExitDuration` changed to 5 years. `SetTier` applies the change without checking `PositionCountByTier`. [1](#0-0) 
4. Alice calls `MsgTriggerExitFromTier`. The keeper fetches the current Tier 1 (`ExitDuration = 5 years`) and calls `pos.TriggerExit(blockTime, 5 years)`. [3](#0-2) 
5. `ExitUnlockAt` is set to `now + 5 years`. Alice cannot call `MsgExitTierWithDelegation` or `MsgTierUndelegate` until that timestamp. [6](#0-5) 
6. Alice's 10,000 CRO is locked for 4 years longer than the term she accepted.

### Citations

**File:** x/tieredrewards/keeper/tier.go (L27-35)
```go
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

**File:** x/tieredrewards/keeper/tier.go (L37-44)
```go
func (k Keeper) deleteTier(ctx context.Context, tierId uint32) error {
	hasPositions, err := k.hasPositionsForTier(ctx, tierId)
	if err != nil {
		return err
	}
	if hasPositions {
		return types.ErrTierHasActivePositions
	}
```

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

**File:** x/tieredrewards/types/position.go (L61-63)
```go
func (p Position) CompletedExitLockDuration(blockTime time.Time) bool {
	return !p.ExitUnlockAt.IsZero() && !blockTime.Before(p.ExitUnlockAt)
}
```

**File:** x/tieredrewards/types/position.go (L71-74)
```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
	p.ExitTriggeredAt = blockTime
	p.ExitUnlockAt = blockTime.Add(duration)
}
```

**File:** x/tieredrewards/keeper/position.go (L56-56)
```go
	pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, true, blockTime)
```
