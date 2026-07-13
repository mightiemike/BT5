### Title
`TierRedelegate` Hardcodes `LastKnownBonded = true`, Bypassing Unbonded-State Bonus Accounting — (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

The `TierRedelegate` message handler unconditionally sets `LastKnownBonded = true` on the position after redelegation, regardless of whether the destination validator is actually bonded. Because `processEventsAndClaimBonus` uses `LastKnownBonded` as the starting bonded state for bonus accrual, a user who redelegates to an unbonded validator will have their position incorrectly treated as bonded from the moment of redelegation, causing the module to overpay bonus rewards from the `RewardsPoolName` pool.

---

### Finding Description

The `processEventsAndClaimBonus` function explicitly relies on `pos.LastKnownBonded` as the authoritative starting bonded state for each bonus-accrual replay:

```go
// Use the persisted bonded state from the last replay, not a hardcoded default.
// This prevents overpaying bonus for unbonded gaps between claims.
bonded := pos.LastKnownBonded
``` [1](#0-0) 

This design is intentional: the comment explicitly states that `LastKnownBonded` prevents overpaying bonus for unbonded gaps.

However, in `TierRedelegate`, after the redelegation is executed, `UpdateBonusCheckpoints` is called with the third argument hardcoded to `true`:

```go
latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
...
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
``` [2](#0-1) 

This unconditionally sets `LastKnownBonded = true` on the position, even when the destination validator is currently unbonded. The `validateRedelegatePosition` function performs no check on the destination validator's bonded status: [3](#0-2) 

The standard Cosmos SDK `BeginRedelegation` (called via `ms.redelegate`) does not require the destination validator to be bonded. Redelegation to an unbonded validator is a valid on-chain operation.

**Contrast with the direct path**: When a position is created via `LockTier` or `CommitDelegationToTier`, `createDelegatedPosition` is called, which sets `lastEventSeq` to the current validator event sequence and `LastBonusAccrual` to `blockTime`. The initial `LastKnownBonded` is implicitly correct because `transferDelegationToPosition` explicitly rejects unbonded validators:

```go
if !validator.IsBonded() {
    return math.LegacyDec{}, types.ErrValidatorNotBonded
}
``` [4](#0-3) 

The `TierRedelegate` path has no equivalent guard and hardcodes `true` instead of reading the destination validator's actual bonded state.

---

### Impact Explanation

After redelegating to an unbonded validator V2:

- `LastEventSeq` is set to V2's latest event sequence (which is already past the UNBOND event that made V2 unbonded).
- `LastKnownBonded` is set to `true` (incorrect; V2 is unbonded).
- `LastBonusAccrual` is set to `blockTime`.

When V2 later becomes bonded again (a BOND event is appended), `processEventsAndClaimBonus` will:

1. Start with `bonded = true` (from the corrupted `LastKnownBonded`).
2. Fetch events since `LastEventSeq` — the first event is the BOND event.
3. Compute bonus for the segment `[LastBonusAccrual, BOND_event_time]` with `bonded = true`. [5](#0-4) 

This segment covers the entire period during which V2 was unbonded — a period for which no bonus should be owed. The module pays this bonus from the `RewardsPoolName` module account, draining it beyond what is legitimately owed.

The corrupted value is: **`pos.LastKnownBonded`** in the `Position` stored under `k.Positions` (a `collections.Map[uint64, types.Position]`). [6](#0-5) 

---

### Likelihood Explanation

**Low.** The attack requires:
1. An existing unbonded validator on the network (validators do get jailed/tombstoned in practice).
2. The attacker must have an active tiered-rewards position.
3. The attacker must call `MsgTierRedelegate` targeting the unbonded validator.
4. The unbonded validator must later re-bond (or the attacker must wait for a BOND event).

All of these are normal, unprivileged on-chain operations. No privileged role or leaked key is required.

---

### Recommendation

In `TierRedelegate`, replace the hardcoded `true` with a runtime check of the destination validator's bonded status before calling `UpdateBonusCheckpoints`:

```go
dstValidator, err := ms.stakingKeeper.GetValidator(ctx, dstValAddr)
if err != nil {
    return nil, err
}
isDstBonded := dstValidator.IsBonded()
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), isDstBonded)
``` [7](#0-6) 

Additionally, `validateRedelegatePosition` should reject redelegation to an unbonded destination validator, consistent with the guard already present in `transferDelegationToPosition`. [3](#0-2) 

---

### Proof of Concept

1. Attacker creates a position via `MsgLockTier` on bonded validator V1. `LastKnownBonded = true`, `LastBonusAccrual = T0`.
2. Validator V2 becomes unbonded (jailed). `AfterValidatorBeginUnbonding` fires, appending an UNBOND event at sequence S_unbond.
3. Attacker calls `MsgTierRedelegate` with `DstValidator = V2`.
   - `claimRewards` runs cleanly against V1 (no issue here).
   - `redelegate` moves delegation to V2 (SDK allows this).
   - `getValidatorEventLatestSeq(V2)` returns S_unbond (the sequence after the UNBOND event).
   - `pos.UpdateBonusCheckpoints(S_unbond, T1, true)` sets `LastKnownBonded = true`, `LastBonusAccrual = T1`.
4. V2 is unjailed and re-bonds at time T2. `AfterValidatorBonded` fires, appending a BOND event at sequence S_bond.
5. Attacker calls `MsgClaimTierRewards`.
   - `processEventsAndClaimBonus` starts with `bonded = true` (from corrupted `LastKnownBonded`).
   - Fetches events since S_unbond → finds the BOND event at S_bond.
   - Computes bonus for segment `[T1, T2]` with `bonded = true` and pays it from `RewardsPoolName`.
   - The segment `[T1, T2]` is entirely within the unbonded period — no bonus should have been paid. [8](#0-7) [9](#0-8)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-213)
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

**File:** x/tieredrewards/keeper/msg_server.go (L210-284)
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

**File:** x/tieredrewards/keeper/transfer_delegation.go (L53-55)
```go
	if !validator.IsBonded() {
		return math.LegacyDec{}, types.ErrValidatorNotBonded
	}
```

**File:** x/tieredrewards/keeper/keeper.go (L97-97)
```go
		Positions:                collections.NewMap(sb, types.PositionsKey, "positions", collections.Uint64Key, codec.CollValue[types.Position](cdc)),
```
