### Title
Governance `DeleteTier` Permanently Breaks All Positions in the Deleted Tier Without Settling Rewards — (`x/tieredrewards/keeper/msg_server_auth.go`)

---

### Summary

When governance calls `MsgDeleteTier`, the tier is removed from state without first claiming accrued bonus rewards for positions in that tier and without checking whether active positions exist. After deletion, every position whose `TierId` references the now-deleted tier is permanently broken: any operation that internally calls `getTier` returns an error, making `TierUndelegate`, `TierRedelegate`, `ExitTierWithDelegation`, `ClaimTierRewards`, and `AddToTierPosition` all fail. Position owners lose their accrued bonus rewards and cannot undelegate or exit their locked funds. There is no self-recovery path for the affected position owners.

---

### Finding Description

`DeleteTier` in `msg_server_auth.go` deletes a tier from state with no pre-deletion reward settlement and no guard against active positions:

```go
func (ms msgServer) DeleteTier(ctx context.Context, msg *types.MsgDeleteTier) (*types.MsgDeleteTierResponse, error) {
    if err := ms.requireAuthority(msg.Authority); err != nil {
        return nil, err
    }
    tier, err := ms.getTier(ctx, msg.Id)
    if err != nil {
        return nil, err
    }
    if err := ms.deleteTier(ctx, msg.Id); err != nil {
        return nil, err
    }
    ...
}
``` [1](#0-0) 

Compare this with `UpdateTier`, which explicitly calls `claimRewardsAndUpdateTierPositions` before changing the tier's `BonusApy`, demonstrating that the developers understood the need to settle rewards before mutating tier state:

```go
if !oldTier.BonusApy.Equal(msg.Tier.BonusApy) {
    if err := ms.claimRewardsAndUpdateTierPositions(ctx, msg.Tier.Id); err != nil {
        return nil, err
    }
}
``` [2](#0-1) 

`DeleteTier` omits this step entirely.

After deletion, every code path that touches a position in the deleted tier calls `getTier(ctx, pos.TierId)`, which now returns an error. The critical path is `processEventsAndClaimBonus`:

```go
tier, err := k.getTier(ctx, pos.TierId)
if err != nil {
    return nil, err
}
``` [3](#0-2) 

`processEventsAndClaimBonus` is called by `claimRewards`, which is called by every exit and claim path:

- `TierUndelegate` → `claimRewards` → fails
- `TierRedelegate` → `claimRewards` → fails
- `ExitTierWithDelegation` → `claimRewards` → fails
- `ClaimTierRewards` → `claimRewardsAndUpdatesPositions` → fails
- `AddToTierPosition` → `claimRewards` → fails [4](#0-3) 

The only remaining path is `WithdrawFromTier`, which does not call `getTier` directly — but it requires the position to already be undelegated. To reach an undelegated state, the owner must first call `TierUndelegate`, which fails. So delegated positions are fully stuck.

---

### Impact Explanation

For every position whose `TierId` matches the deleted tier:

1. **Accrued bonus rewards are permanently lost.** `ClaimTierRewards` fails; the rewards remain in the `RewardsPoolName` module account and are inaccessible to the position owner.
2. **Delegated funds are locked.** `TierUndelegate` and `ExitTierWithDelegation` both fail, so the position's delegation cannot be returned to the owner. The locked tokens are stuck in the position's delegator sub-account indefinitely.

The position owner has no self-recovery path. Only a subsequent governance proposal to re-add the tier with the same ID could restore functionality, but even then the accrued rewards for the gap period may be miscalculated.

---

### Likelihood Explanation

Governance can submit and pass a `MsgDeleteTier` proposal for any existing tier at any time. The message requires only the governance authority address, which is the standard `x/gov` module account reachable via an on-chain proposal. No leaked keys or social engineering are required. A governance mistake (e.g., deleting a tier believed to be empty) or a malicious proposal is sufficient to trigger the bug. The impact scales with the number of active positions in the deleted tier.

---

### Recommendation

1. **Guard against active positions:** Before deleting a tier, check that no positions reference it. Return an error if any exist.
2. **Settle rewards before deletion:** Call `claimRewardsAndUpdateTierPositions(ctx, msg.Id)` in `DeleteTier` before removing the tier, mirroring the pattern already used in `UpdateTier`.
3. **Provide a fallback exit path:** Add a variant of `TierUndelegate` / `ExitTierWithDelegation` that skips bonus reward computation when the tier no longer exists, so position owners can always recover their principal.

---

### Proof of Concept

1. Governance creates Tier ID 1 with `BonusApy > 0`.
2. Alice calls `MsgLockTier` for Tier 1, locking 10,000 CRO and delegating to a validator. Her position ID is 42.
3. Several blocks pass; Alice accrues base and bonus rewards.
4. Governance passes `MsgDeleteTier{Id: 1}`. `DeleteTier` removes the tier without calling `claimRewardsAndUpdateTierPositions`.
5. Alice calls `MsgClaimTierRewards{PositionIds: [42]}`. Execution reaches `processEventsAndClaimBonus` → `getTier(ctx, 1)` → `ErrTierNotFound`. Transaction reverts. Rewards are lost.
6. Alice calls `MsgTierUndelegate{PositionId: 42}`. Same failure path. Her 10,000 CRO delegation is permanently stuck.
7. Alice has no message she can send to recover her funds without a new governance proposal. [1](#0-0) [5](#0-4) [4](#0-3)

### Citations

**File:** x/tieredrewards/keeper/msg_server_auth.go (L67-71)
```go
	if !oldTier.BonusApy.Equal(msg.Tier.BonusApy) {
		if err := ms.claimRewardsAndUpdateTierPositions(ctx, msg.Tier.Id); err != nil {
			return nil, err
		}
	}
```

**File:** x/tieredrewards/keeper/msg_server_auth.go (L84-103)
```go
func (ms msgServer) DeleteTier(ctx context.Context, msg *types.MsgDeleteTier) (*types.MsgDeleteTierResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	tier, err := ms.getTier(ctx, msg.Id)
	if err != nil {
		return nil, err
	}

	if err := ms.deleteTier(ctx, msg.Id); err != nil {
		return nil, err
	}

	if err := ms.emitTierChangedEvent(ctx, types.TierChangeAction_TIER_CHANGE_ACTION_DELETE, tier); err != nil {
		return nil, err
	}

	return &types.MsgDeleteTierResponse{}, nil
}
```

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

**File:** x/tieredrewards/keeper/msg_server.go (L152-208)
```go
func (ms msgServer) TierUndelegate(ctx context.Context, msg *types.MsgTierUndelegate) (*types.MsgTierUndelegateResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateUndelegatePosition(ctx, pos, msg.Owner); err != nil {
		return nil, err
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}

	srcValidator := pos.Delegation.ValidatorAddress
	valAddr, err := sdk.ValAddressFromBech32(srcValidator)
	if err != nil {
		return nil, err
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	completionTime, _, err := ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
	}

	pos.ClearBonusCheckpoints()

	if err := ms.setPosition(ctx, pos.Position, &ValidatorTransition{PreviousAddress: srcValidator}); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionUndelegated{
		PositionId:     pos.Id,
		TierId:         pos.TierId,
		Owner:          pos.Owner,
		Validator:      srcValidator,
		CompletionTime: completionTime,
	}); err != nil {
		return nil, err
	}

	return &types.MsgTierUndelegateResponse{
		CompletionTime: completionTime,
		PositionId:     pos.Id,
	}, nil
}
```
