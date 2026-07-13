### Title
Hardcoded `LastKnownBonded=true` in `TierRedelegate` Enables Bonus Theft When Destination Validator Is Unbonded — (`x/tieredrewards/keeper/msg_server.go`)

### Summary

`TierRedelegate` unconditionally calls `pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)` with the third argument hardcoded to `true`, setting `LastKnownBonded=true` regardless of the destination validator's actual bonded status. When the destination validator is unbonded at redelegation time and later re-bonds, `processEventsAndClaimBonus` incorrectly treats the entire gap between redelegation and re-bonding as a bonded segment and pays bonus rewards for it — a period during which no bonus should have accrued.

---

### Finding Description

In `TierRedelegate`, after the SDK-level redelegation completes, the position's bonus checkpoint is reset:

```go
// msg_server.go line 263
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
``` [1](#0-0) 

`UpdateBonusCheckpoints` writes all three fields directly:

```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
    p.LastEventSeq = lastEventSeq
    p.LastBonusAccrual = t
    p.LastKnownBonded = lastKnownBonded
}
``` [2](#0-1) 

`latestSeq` is the **current** latest event sequence for the destination validator — meaning it points past all existing events, including any prior UNBOND event. The hardcoded `true` asserts the validator is bonded at this moment, even if it is not.

`validateRedelegatePosition` has **no check** on the destination validator's bonded status: [3](#0-2) 

When `processEventsAndClaimBonus` runs next, it seeds the replay with the persisted `LastKnownBonded`:

```go
bonded := pos.LastKnownBonded   // = true (wrong)
segmentStart := pos.LastBonusAccrual  // = time of redelegate
``` [4](#0-3) 

When the first post-redelegation event is processed (e.g., a BOND event when the validator re-bonds), the loop body executes with `bonded=true`:

```go
if bonded {
    bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
    totalBonus = totalBonus.Add(bonus)
}
``` [5](#0-4) 

This computes bonus for the segment `[T_redelegate, T_rebond]` — a period when the validator was actually unbonded — and pays it out from the rewards pool.

---

### Impact Explanation

Bonus rewards are paid from `RewardsPoolName` to the attacker's address for time intervals during which the destination validator was unbonded. The invariant "bonus only accrues during bonded validator segments" is violated. The economic loss is bounded by `(T_rebond - T_redelegate) × bonusRate × positionAmount`, which can be material for long unbonding windows or large positions. [6](#0-5) 

---

### Likelihood Explanation

Validators are jailed and unjailed regularly on live chains. An attacker with a tiered position only needs to observe a currently-unbonded validator and submit `MsgTierRedelegate` pointing to it. No governance, privileged role, or key compromise is required — it is a standard user transaction.

---

### Recommendation

Replace the hardcoded `true` with the actual bonded status of the destination validator at the time of redelegation:

```go
dstVal, err := ms.stakingKeeper.GetValidator(ctx, dstValAddr)
if err != nil {
    return nil, err
}
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), dstVal.IsBonded())
```

This mirrors the correct pattern used in `processEventsAndClaimBonus`, which already performs a live `val.IsBonded()` check before computing the trailing segment. [7](#0-6) 

---

### Proof of Concept

1. Create a keeper integration test with two validators: `srcVal` (bonded) and `dstVal` (unbonded — jailed or never bonded).
2. Lock a tier position delegated to `srcVal`.
3. Call `MsgTierRedelegate` with `DstValidator = dstVal`. Observe `pos.LastKnownBonded = true` and `pos.LastEventSeq = latestSeq` for `dstVal`.
4. Advance several blocks (simulating time passing while `dstVal` is still unbonded).
5. Unjail / re-bond `dstVal` — this appends a BOND event at `seq = latestSeq+1`.
6. Call `MsgClaimTierRewards`. Assert `bonusRewards > 0`.
7. The test should fail (bonus should be zero for the unbonded gap), confirming the vulnerability.

The relevant checkpoint state after step 3 is:

```
pos.LastKnownBonded  = true   // hardcoded, wrong
pos.LastEventSeq     = N      // points past the UNBOND event
pos.LastBonusAccrual = T1     // time of redelegate
```

When the BOND event at seq `N+1` (time `T2`) is processed, `bonded=true` causes `computeSegmentBonus(T1, T2, ...)` to fire, paying bonus for the entire unbonded gap `[T1, T2]`. [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L257-265)
```go
	latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)

	if err := ms.setPosition(ctx, pos.Position, &ValidatorTransition{PreviousAddress: srcValidator}); err != nil {
```

**File:** x/tieredrewards/types/position.go (L65-69)
```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
	p.LastEventSeq = lastEventSeq
	p.LastBonusAccrual = t
	p.LastKnownBonded = lastKnownBonded
}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L67-103)
```go
func (k Keeper) validateRedelegatePosition(ctx context.Context, pos types.PositionState, owner, dstValidator string) error {
	if !pos.IsOwner(owner) {
		return types.ErrNotPositionOwner
	}

	if !pos.IsDelegated() {
		return types.ErrPositionNotDelegated
	}

	if pos.Delegation.ValidatorAddress == dstValidator {
		return types.ErrRedelegationToSameValidator
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if pos.HasTriggeredExit() && pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationElapsed
	}

	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return err
	}

	if tier.IsCloseOnly() {
		return types.ErrTierIsCloseOnly
	}

	isRedelegating, err := k.isRedelegating(ctx, pos.DelegatorAddress)
	if err != nil {
		return err
	}
	if isRedelegating {
		return errorsmod.Wrapf(types.ErrActiveRedelegation, "position %d has an active redelegation", pos.Id)
	}

	return nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L163-165)
```go
	// This prevents overpaying bonus for unbonded gaps between claims.
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L175-179)
```go
		if bonded {
			// Compute bonus for the bonded segment [segmentStart, eventTime]
			// using the snapshot rate at the event.
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L201-213)
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
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L237-240)
```go
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
```
