Based on my analysis of the `x/tieredrewards` module, I found a concrete analog.

---

### Title
Missing Deadline Parameter in `MsgTriggerExitFromTier` Allows Delayed Transactions to Extend Lock Duration Beyond User's Intention - (File: x/tieredrewards/keeper/msg_server.go)

### Summary

`MsgTriggerExitFromTier` sets `ExitUnlockAt = block.timestamp + tier.ExitDuration` with no user-supplied deadline. If the transaction is delayed in the mempool (gas price spike, validator inclusion lag, network congestion), the resulting `ExitUnlockAt` is pushed further into the future than the user intended, locking their staked CRO for longer than expected and without their control.

### Finding Description

When a user calls `MsgTriggerExitFromTier`, the message server unconditionally uses the block time at execution to compute the unlock timestamp:

```go
sdkCtx := sdk.UnwrapSDKContext(ctx)
pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)
``` [1](#0-0) 

`TriggerExit` sets `ExitUnlockAt = blockTime + tier.ExitDuration`. There is no `latestTriggerTime` or deadline field in `MsgTriggerExitFromTier`:

```go
type MsgTriggerExitFromTier struct {
    Owner      string `protobuf:"bytes,1,opt,name=owner,proto3"`
    PositionId uint64 `protobuf:"varint,2,opt,name=position_id,json=positionId,proto3"`
}
``` [2](#0-1) 

The `ExitUnlockAt` field on the `Position` struct is the sole gate controlling when `MsgTierUndelegate` and `MsgExitTierWithDelegation` become callable: [3](#0-2) 

The validation for `MsgTierUndelegate` enforces that `block.timestamp >= ExitUnlockAt` before allowing undelegation: [4](#0-3) 

Because `ExitUnlockAt` is derived entirely from the block time at execution, any delay between when the user signs and broadcasts the transaction and when it is actually included in a block directly extends the lock duration by the same amount.

### Impact Explanation

A user's staked CRO (held in the derived `PositionDelegatorAccount`) remains locked and undelegatable until `ExitUnlockAt` passes. If the transaction is delayed by `D` seconds, the user's funds are locked for `ExitDuration + D` instead of the intended `ExitDuration`. For tiers with long `ExitDuration` values (e.g., weeks or months), even a modest delay of hours or days is material. The user has no on-chain mechanism to cancel or bound the execution window of the trigger transaction. [5](#0-4) 

### Likelihood Explanation

Cosmos SDK transactions can sit in the mempool for extended periods during gas price spikes or validator downtime. The `TriggerExitFromTier` operation is time-sensitive by design — users call it precisely when they want to begin the countdown to unlock. Any user who submits this transaction near a period of network congestion will have their unlock time silently extended. No privileged access or attacker is required; the delay is a natural network condition.

### Recommendation

Add a `latest_trigger_time` (deadline) field to `MsgTriggerExitFromTier`. In the message server, reject the transaction if `sdkCtx.BlockTime() > msg.LatestTriggerTime`. This mirrors the standard deadline pattern used in AMM swap messages and gives users a bounded execution window.

```go
if !msg.LatestTriggerTime.IsZero() && sdkCtx.BlockTime().After(msg.LatestTriggerTime) {
    return nil, types.ErrTriggerDeadlineExceeded
}
pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)
```

The same pattern should be applied to `MsgLockTier` and `MsgCommitDelegationToTier`, since `TriggerExitImmediately=true` on those messages also calls `TriggerExit` at execution time. [6](#0-5) 

### Proof of Concept

1. Tier T has `ExitDuration = 30 days`.
2. User U holds a position and wants their funds available in exactly 30 days. They sign and broadcast `MsgTriggerExitFromTier` at block time `T0`.
3. Due to a gas price spike, the transaction sits in the mempool for 2 days and is included at block time `T0 + 2 days`.
4. `ExitUnlockAt` is set to `T0 + 2 days + 30 days = T0 + 32 days`.
5. U cannot call `MsgTierUndelegate` or `MsgExitTierWithDelegation` until `T0 + 32 days` — 2 days later than intended, with no recourse.
6. U had no way to specify "only execute this if block time ≤ T0 + 1 hour", so the stale transaction executes with an unintended unlock time. [1](#0-0)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L65-66)
```go
	pos, err := ms.createDelegatedPosition(ctx, msg.Owner, tier, valAddr, delAddr, msg.TriggerExitImmediately)
	if err != nil {
```

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

**File:** x/tieredrewards/types/tx.pb.go (L619-625)
```go
// MsgTierUndelegate begins undelegating a position's tokens from its validator.
type MsgTierUndelegate struct {
	// owner is the position owner's address.
	Owner string `protobuf:"bytes,1,opt,name=owner,proto3" json:"owner,omitempty"`
	// position_id is the ID of the position to undelegate.
	PositionId uint64 `protobuf:"varint,2,opt,name=position_id,json=positionId,proto3" json:"position_id,omitempty"`
}
```

**File:** x/tieredrewards/types/types.pb.go (L155-156)
```go
	// exit_unlock_at is when the user can claim tokens (exit_triggered_at + tier.exit_duration).
	ExitUnlockAt time.Time `protobuf:"bytes,8,opt,name=exit_unlock_at,json=exitUnlockAt,proto3,stdtime" json:"exit_unlock_at"`
```

**File:** x/tieredrewards/keeper/msg_validate.go (L1-5)
```go
package keeper

import (
	"context"

```
