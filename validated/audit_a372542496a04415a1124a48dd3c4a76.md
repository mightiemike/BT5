### Title
`MsgTriggerExitFromTier` Silently Applies Governance-Changed `ExitDuration` Without User Consent — (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`MsgTriggerExitFromTier` reads `tier.ExitDuration` from live on-chain state at execution time and carries no user-supplied expected value. If governance executes `MsgUpdateTier` (increasing `ExitDuration`) in a block ordered before the user's trigger-exit transaction, the user's `ExitUnlockAt` is silently set to a far-future timestamp the user never consented to, locking their funds for a materially longer period.

---

### Finding Description

In `TriggerExitFromTier`, the handler fetches the current tier and applies its live `ExitDuration`:

```go
// x/tieredrewards/keeper/msg_server.go  lines 361-367
tier, err := ms.getTier(ctx, pos.TierId)
if err != nil {
    return nil, err
}
sdkCtx := sdk.UnwrapSDKContext(ctx)
pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)
``` [1](#0-0) 

`MsgTriggerExitFromTier` carries only `owner` and `position_id`:

```go
func (msg MsgTriggerExitFromTier) Validate() error {
    if _, err := sdk.AccAddressFromBech32(msg.Owner); err != nil { ... }
    return nil
}
``` [2](#0-1) 

There is no `expected_exit_duration` field. The message carries no commitment to the tier parameters the user observed when they signed.

Governance can change `ExitDuration` via `MsgUpdateTier`, which is authority-gated and executes atomically when a governance proposal passes:

```go
func (ms msgServer) UpdateTier(ctx context.Context, msg *types.MsgUpdateTier) (*types.MsgUpdateTierResponse, error) {
    ...
    if err := ms.SetTier(ctx, msg.Tier); err != nil { ... }
    ...
}
``` [3](#0-2) 

The `Tier` struct stores `ExitDuration` as a mutable field:

```go
type Tier struct {
    Id            uint32
    ExitDuration  time.Duration
    BonusApy      cosmossdk_io_math.LegacyDec
    MinLockAmount cosmossdk_io_math.Int
    CloseOnly     bool
}
``` [4](#0-3) 

The same TOCTOU gap also exists in `MsgLockTier` when `trigger_exit_immediately = true`, because `createDelegatedPosition` calls `pos.TriggerExit(blockTime, tier.ExitDuration)` with the same live tier value:

```go
if triggerExitImmediately {
    pos.TriggerExit(blockTime, tier.ExitDuration)
}
``` [5](#0-4) 

---

### Impact Explanation

`pos.TriggerExit` sets `ExitUnlockAt = block_time + tier.ExitDuration`. If governance increases `ExitDuration` from 1 year to 5 years and the proposal executes in a block ordered before the user's `MsgTriggerExitFromTier`, the user's `ExitUnlockAt` is stamped 5 years in the future instead of 1 year. The user cannot call `TierUndelegate`, `WithdrawFromTier`, or `ExitTierWithDelegation` until `block_time >= ExitUnlockAt`. Their staked funds are inaccessible for 4 additional years beyond what they consented to. The corrupted value is `Position.ExitUnlockAt` stored in the `Positions` collection.

---

### Likelihood Explanation

Cosmos SDK governance proposals require a deposit period and a voting period (typically 5–7 days total), so the window is not a single block. However, the scenario is realistic when:

1. A governance proposal to extend `ExitDuration` is in its final voting hours.
2. A user, unaware the proposal is about to pass, signs and broadcasts `MsgTriggerExitFromTier`.
3. The proposal's `EndBlocker` execution lands in a block before the user's transaction is included (e.g., due to low gas price or mempool ordering).
4. The user's transaction executes against the updated tier.

Expedited governance proposals (supported by the CLI) shorten the window further. The user has no on-chain mechanism to express "only trigger exit if `ExitDuration` is still X".

---

### Recommendation

Add an optional `expected_exit_duration` field to `MsgTriggerExitFromTier`. In the handler, after fetching the tier, check:

```go
if msg.ExpectedExitDuration != 0 && tier.ExitDuration != msg.ExpectedExitDuration {
    return nil, errorsmod.Wrapf(types.ErrTierParamsMismatch,
        "expected exit_duration %s, got %s", msg.ExpectedExitDuration, tier.ExitDuration)
}
```

Apply the same guard to `MsgLockTier` when `trigger_exit_immediately = true`. This mirrors the fix described in the reference report: compare the user-supplied expected value against the live on-chain value and revert if they differ.

---

### Proof of Concept

1. Tier 1 is configured with `ExitDuration = 1 year`.
2. User holds position `#42` in Tier 1 and decides to trigger exit.
3. User signs and broadcasts `MsgTriggerExitFromTier{owner: user, position_id: 42}`.
4. In the same or a subsequent block, a governance proposal executing `MsgUpdateTier{tier: {id: 1, exit_duration: 5 years, ...}}` is processed first.
5. The user's `MsgTriggerExitFromTier` is then executed.
6. `ms.getTier(ctx, pos.TierId)` returns the updated tier with `ExitDuration = 5 years`.
7. `pos.TriggerExit(blockTime, tier.ExitDuration)` sets `ExitUnlockAt = blockTime + 5 years`.
8. The user's position is now locked until `blockTime + 5 years`; all exit paths (`TierUndelegate`, `ExitTierWithDelegation`, `WithdrawFromTier`) are gated on `CompletedExitLockDuration` and will revert for 4 additional years. [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L347-386)
```go
func (ms msgServer) TriggerExitFromTier(ctx context.Context, msg *types.MsgTriggerExitFromTier) (*types.MsgTriggerExitFromTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateTriggerExit(pos.Position, msg.Owner); err != nil {
		return nil, err
	}

	tier, err := ms.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)

	if err := ms.setPosition(ctx, pos.Position, nil); err != nil {
		return nil, err
	}

	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventExitTriggered{
		PositionId:   pos.Id,
		TierId:       pos.TierId,
		Owner:        pos.Owner,
		ExitUnlockAt: pos.ExitUnlockAt,
	}); err != nil {
		return nil, err
	}

	return &types.MsgTriggerExitFromTierResponse{
		ExitUnlockAt: pos.ExitUnlockAt,
		PositionId:   pos.Id,
	}, nil
}
```

**File:** x/tieredrewards/types/msgs.go (L99-105)
```go
func (msg MsgTriggerExitFromTier) Validate() error {
	if _, err := sdk.AccAddressFromBech32(msg.Owner); err != nil {
		return errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	return nil
}
```

**File:** x/tieredrewards/keeper/msg_server_auth.go (L57-81)
```go
func (ms msgServer) UpdateTier(ctx context.Context, msg *types.MsgUpdateTier) (*types.MsgUpdateTierResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	oldTier, err := ms.getTier(ctx, msg.Tier.Id)
	if err != nil {
		return nil, err
	}

	if !oldTier.BonusApy.Equal(msg.Tier.BonusApy) {
		if err := ms.claimRewardsAndUpdateTierPositions(ctx, msg.Tier.Id); err != nil {
			return nil, err
		}
	}

	if err := ms.SetTier(ctx, msg.Tier); err != nil {
		return nil, err
	}

	if err := ms.emitTierChangedEvent(ctx, types.TierChangeAction_TIER_CHANGE_ACTION_UPDATE, msg.Tier); err != nil {
		return nil, err
	}

	return &types.MsgUpdateTierResponse{}, nil
```

**File:** x/tieredrewards/types/types.pb.go (L68-80)
```go
type Tier struct {
	// id is the unique identifier for this tier.
	Id uint32 `protobuf:"varint,1,opt,name=id,proto3" json:"id,omitempty"`
	// exit_duration is the duration a user must wait after triggering exit before they can claim.
	ExitDuration time.Duration `protobuf:"bytes,2,opt,name=exit_duration,json=exitDuration,proto3,stdduration" json:"exit_duration"`
	// bonus_apy is the fixed bonus APY (per year) for this tier, e.g. "0.04" = 4%.
	BonusApy cosmossdk_io_math.LegacyDec `protobuf:"bytes,3,opt,name=bonus_apy,json=bonusApy,proto3,customtype=cosmossdk.io/math.LegacyDec" json:"bonus_apy"`
	// min_lock_amount is the minimum amount (in bond denom) required when creating a new position in this tier.
	MinLockAmount cosmossdk_io_math.Int `protobuf:"bytes,4,opt,name=min_lock_amount,json=minLockAmount,proto3,customtype=cosmossdk.io/math.Int" json:"min_lock_amount"`
	// close_only when true prevents new positions from being created in this tier.
	// Existing positions can still trigger exit, undelegate, withdraw rewards, and claim.
	CloseOnly bool `protobuf:"varint,5,opt,name=close_only,json=closeOnly,proto3" json:"close_only,omitempty"`
}
```

**File:** x/tieredrewards/keeper/position.go (L67-69)
```go
	if triggerExitImmediately {
		pos.TriggerExit(blockTime, tier.ExitDuration)
	}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L44-64)
```go
func (k Keeper) validateUndelegatePosition(ctx context.Context, pos types.PositionState, owner string) error {
	if !pos.IsOwner(owner) {
		return types.ErrNotPositionOwner
	}

	if !pos.IsDelegated() {
		return types.ErrPositionNotDelegated
	}

	if !pos.HasTriggeredExit() {
		return types.ErrExitNotTriggered
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if !pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationNotReached
	}

	// skip check for zero amount as we want those positions to be able to close their position properly

	return nil
```
