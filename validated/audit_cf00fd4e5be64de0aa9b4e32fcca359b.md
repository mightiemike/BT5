### Title
Hardcoded `LastKnownBonded = true` in `TierRedelegate` Causes Bonus Overpayment When Redelegating to Unbonded Validator — (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

In `MsgTierRedelegate`, after claiming rewards and moving the delegation to the destination validator, `UpdateBonusCheckpoints` is called with `LastKnownBonded` hardcoded to `true`, regardless of the actual bonded state of the destination validator. If the destination validator is unbonded at the time of redelegate, the position's `LastKnownBonded` is set incorrectly to `true`. The next `processEventsAndClaimBonus` call then starts with `bonded = true` and computes bonus for the period from `LastBonusAccrual` to the first subsequent BOND event, even though the validator was actually unbonded during that entire period — resulting in bonus being paid from the rewards pool for time the validator was not bonded.

---

### Finding Description

In `msg_server.go`, after `TierRedelegate` claims rewards and moves the delegation to the destination validator, it resets the position's bonus checkpoints:

```go
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
``` [1](#0-0) 

The third argument `true` hardcodes `LastKnownBonded = true` unconditionally. This is the persisted bonded state used as the starting point for the next `processEventsAndClaimBonus` call:

```go
bonded := pos.LastKnownBonded
segmentStart := pos.LastBonusAccrual
``` [2](#0-1) 

`latestSeq` is set to the latest event sequence number for the destination validator, meaning all events up to and including that sequence — including any UNBOND event that put the validator into an unbonded state — are marked as already processed and will be skipped in the next replay. With `LastKnownBonded = true` and the UNBOND event skipped, the next `processEventsAndClaimBonus` starts with `bonded = true` even though the validator is actually unbonded.

When the validator later becomes bonded (a BOND event at seq `S > latestSeq`), the event loop processes it:

```go
if bonded {
    bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
    totalBonus = totalBonus.Add(bonus)
}
``` [3](#0-2) 

Since `bonded = true` (incorrectly), bonus is computed for the segment `[LastBonusAccrual, BOND_event_time]`, which spans the entire unbonded period. The validator was actually unbonded during this entire window, so no bonus should have accrued.

The correct fix — checking the actual bonded state of the destination validator — is already applied correctly in `processEventsAndClaimBonus` itself via `pos.UpdateLastKnownBonded(bonded)` at line 217, but `TierRedelegate` overwrites this with a hardcoded `true` after the claim. [4](#0-3) 

---

### Impact Explanation

An attacker can claim bonus rewards from the `RewardsPoolName` module account for time periods during which their position's validator was unbonded. The corrupted value is the `bonus` amount sent via `bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins)`. This drains the rewards pool faster than the protocol intends, directly harming other legitimate reward claimants. [5](#0-4) 

---

### Likelihood Explanation

The trigger requires redelegating a tier position to a validator that is currently unbonded (jailed). The Cosmos SDK staking module permits redelegation to unbonded validators — there is no check in the `MsgTierRedelegate` validation path (per the ADR reference table) requiring the destination validator to be bonded. The attacker then waits for the validator to unjail and become bonded, then claims rewards. This is a realistic, unprivileged, on-chain sequence of standard transactions. [6](#0-5) 

---

### Recommendation

Replace the hardcoded `true` with the actual bonded state of the destination validator at the time of redelegate:

```go
dstVal, err := ms.stakingKeeper.GetValidator(ctx, dstValAddr)
if err != nil {
    return nil, err
}
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), dstVal.IsBonded())
```

This mirrors the correct pattern already used in `processEventsAndClaimBonus`, where `pos.UpdateLastKnownBonded(bonded)` persists the actual observed bonded state rather than assuming it. [7](#0-6) 

---

### Proof of Concept

1. Validator A is bonded. User creates a tier position delegated to A. `LastKnownBonded = true`, `LastBonusAccrual = T0`.
2. Validator B is jailed/unbonded. Its latest event seq is `latestSeq_B`, which includes the UNBOND event.
3. User calls `MsgTierRedelegate` to move position to validator B.
   - `claimRewards` runs: `processEventsAndClaimBonus` correctly sets `LastKnownBonded` based on A's events, `LastBonusAccrual = T_redelegate`.
   - `UpdateBonusCheckpoints(latestSeq_B, T_redelegate, true)` overwrites: `LastKnownBonded = true` (WRONG — B is unbonded), `LastEventSeq = latestSeq_B` (UNBOND event skipped).
4. Validator B unjails and becomes bonded at time `T_bond` (BOND event at seq `S > latestSeq_B`).
5. User calls `MsgClaimTierRewards`.
   - `processEventsAndClaimBonus` starts with `bonded = true`, `segmentStart = T_redelegate`.
   - BOND event at seq S is processed: `bonded = true` → bonus computed for `[T_redelegate, T_bond]`.
   - Validator B was actually unbonded during `[T_redelegate, T_bond]` — no bonus should have accrued.
   - User receives bonus for the full unbonded gap, paid from the rewards pool. [1](#0-0) [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L210-283)
```go
func (ms msgServer) TierRedelegate(ctx context.Context, msg *types.MsgTierRedelegate) (*types.MsgTierRedelegateResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateRedelegatePosition(ctx, pos, msg.Owner, msg.DstValidator); err != nil {
		return nil, err
	}

	dstValAddr, err := sdk.ValAddressFromBech32(msg.DstValidator)
	if err != nil {
		return nil, err
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}

	srcValidator := pos.Delegation.ValidatorAddress
	srcValAddr, err := sdk.ValAddressFromBech32(srcValidator)
	if err != nil {
		return nil, err
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	completionTime, unbondingID, err := ms.redelegate(ctx, delAddr, srcValAddr, dstValAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
	}
	// unbondingID 0 means the source validator is unbonded; redelegation is instant.
	// We skip mapping because no asynchronous completion hook will trigger.
	if unbondingID != 0 {
		if err := ms.setRedelegationMapping(ctx, unbondingID, pos.Id); err != nil {
			return nil, err
		}
	}

	latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)

	if err := ms.setPosition(ctx, pos.Position, &ValidatorTransition{PreviousAddress: srcValidator}); err != nil {
		return nil, err
	}

	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionRedelegated{
		PositionId:     pos.Id,
		TierId:         pos.TierId,
		Owner:          pos.Owner,
		SrcValidator:   srcValidator,
		DstValidator:   msg.DstValidator,
		CompletionTime: completionTime,
	}); err != nil {
		return nil, err
	}

	return &types.MsgTierRedelegateResponse{
		CompletionTime: completionTime,
		PositionId:     pos.Id,
	}, nil
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L164-165)
```go
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-199)
```go
	for _, entry := range events {
		evt := entry.Event

		if bonded {
			// Compute bonus for the bonded segment [segmentStart, eventTime]
			// using the snapshot rate at the event.
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
		}

		// Update bonded state based on event type.
		switch evt.EventType {
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND:
			bonded = false
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND:
			bonded = true
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_SLASH:
			// Slash doesn't change bonded state.
		}

		segmentStart = evt.Timestamp
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-217)
```go
	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-239)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
```

**File:** x/tieredrewards/types/position.go (L65-68)
```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
	p.LastEventSeq = lastEventSeq
	p.LastBonusAccrual = t
	p.LastKnownBonded = lastKnownBonded
```
