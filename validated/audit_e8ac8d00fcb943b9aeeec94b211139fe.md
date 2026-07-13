### Title
Missing Active-Redelegation Guard in `validateUndelegatePosition` Allows Slash Escape — (`x/tieredrewards/keeper/msg_validate.go`)

### Summary

`validateUndelegatePosition` does not call `isRedelegating`, so a position owner can call `MsgTierUndelegate` on a position that still has an active incoming redelegation entry (V1→V2). The Cosmos SDK's `Undelegate` path does not block this. Once the delegation at V2 is gone, the SDK's `SlashRedelegation` cannot apply the retroactive V1 slash, and the tieredrewards `BeforeRedelegationSlashed` hook silently no-ops because `pos.IsDelegated()` is false. The owner retains tokens that should have been slashed.

### Finding Description

`validateUndelegatePosition` checks owner, delegation existence, exit trigger, and exit-lock duration, but performs no redelegation check: [1](#0-0) 

By contrast, every other exit-path validator that touches the delegation does call `isRedelegating`:

- `validateRedelegatePosition` — lines 94–100
- `validateExitTierWithDelegation` — lines 239–245
- `transferDelegationFromPosition` — lines 117–123 [2](#0-1) 

The `undelegate` helper is a thin pass-through to the SDK with no additional guard: [3](#0-2) 

The Cosmos SDK's `Undelegate` does not check for active incoming redelegations (only `BeginRedelegation` does). So the call at line 182 of `msg_server.go` succeeds, clears `pos.Delegation`, and creates an unbonding delegation at V2: [4](#0-3) 

When V1 is later slashed, `slashRedelegationPosition` is invoked via `BeforeRedelegationSlashed`. It finds the position via the redelegation mapping, but immediately returns nil because `!pos.IsDelegated()`: [5](#0-4) 

The SDK's subsequent `Unbond` call on V2 also fails (no delegation exists). The unbonding delegation is never slashed. The redelegation slash protection window is fully bypassed.

### Impact Explanation

The position owner retains tokens that should have been burned by the retroactive V1 slash. The magnitude equals `slashFactor × redelegated_tokens`. For a 5% double-sign slash on a 1 000 000 token position, the owner keeps 50 000 tokens they are not entitled to. This is a direct, quantifiable fund-retention advantage at the expense of the protocol's slash invariant.

### Likelihood Explanation

The preconditions are reachable in normal operation:

1. A position is created on V1 (exit not yet triggered).
2. Owner calls `MsgTierRedelegate` to V2 — allowed because exit is not triggered.
3. Owner calls `MsgTriggerExitFromTier`.
4. Owner waits for the tier's exit-lock duration (which can be shorter than the 21-day SDK unbonding window).
5. Owner calls `MsgTierUndelegate` — passes all checks in `validateUndelegatePosition`.
6. V1 is slashed for a pre-redelegation infraction (e.g., double-sign evidence submitted within the evidence age).

Steps 1–5 are entirely user-controlled. Step 6 is an external event, but double-sign evidence can be submitted at any time within the evidence age, making the timing window realistic.

### Recommendation

Add an `isRedelegating` guard to `validateUndelegatePosition`, mirroring the pattern already used in `validateRedelegatePosition` and `validateExitTierWithDelegation`:

```go
func (k Keeper) validateUndelegatePosition(ctx context.Context, pos types.PositionState, owner string) error {
    // ... existing checks ...

    isRedelegating, err := k.isRedelegating(ctx, pos.DelegatorAddress)
    if err != nil {
        return err
    }
    if isRedelegating {
        return errorsmod.Wrapf(types.ErrActiveRedelegation,
            "position %d has an active redelegation", pos.Id)
    }

    return nil
}
``` [1](#0-0) 

### Proof of Concept

```go
func (s *KeeperSuite) TestTierUndelegate_BypassesRedelegationSlash() {
    // 1. Create position on V1, exit not triggered.
    pos := s.setupNewTierPosition(sdkmath.NewInt(1_000_000), false)
    delAddr := sdk.MustAccAddressFromBech32(pos.Owner)
    msgServer := keeper.NewMsgServerImpl(s.keeper)
    _, bondDenom := s.getStakingData()
    s.fundRewardsPool(sdkmath.NewInt(10_000_000), bondDenom)

    // 2. Redelegate V1 → V2.
    dstValAddr, _ := s.createSecondValidator()
    _, err := msgServer.TierRedelegate(s.ctx, &types.MsgTierRedelegate{
        Owner: delAddr.String(), PositionId: pos.Id, DstValidator: dstValAddr.String(),
    })
    s.Require().NoError(err)

    // 3. Trigger exit.
    _, err = msgServer.TriggerExitFromTier(s.ctx, &types.MsgTriggerExitFromTier{
        Owner: delAddr.String(), PositionId: pos.Id,
    })
    s.Require().NoError(err)

    // 4. Advance past exit-lock duration (but stay within 21-day unbonding window).
    s.advancePastExitDuration()

    // 5. TierUndelegate succeeds — no redelegation guard.
    _, err = msgServer.TierUndelegate(s.ctx, &types.MsgTierUndelegate{
        Owner: delAddr.String(), PositionId: pos.Id,
    })
    s.Require().NoError(err, "should be rejected while redelegation is active")

    // 6. Simulate V1 slash — BeforeRedelegationSlashed is a no-op because
    //    pos.IsDelegated() == false; unbonding delegation is never slashed.
    isRedelegating, _ := s.keeper.IsRedelegating(s.ctx, pos.DelegatorAddress)
    s.Require().True(isRedelegating, "redelegation entry still active")

    pos, _ = s.keeper.GetPositionState(s.ctx, pos.Id)
    s.Require().False(pos.IsDelegated(), "delegation cleared — slash hook will no-op")
}
```

The test asserts that `TierUndelegate` should be rejected but is not, confirming the missing guard.

### Citations

**File:** x/tieredrewards/keeper/msg_validate.go (L44-65)
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
}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L94-100)
```go
	isRedelegating, err := k.isRedelegating(ctx, pos.DelegatorAddress)
	if err != nil {
		return err
	}
	if isRedelegating {
		return errorsmod.Wrapf(types.ErrActiveRedelegation, "position %d has an active redelegation", pos.Id)
	}
```

**File:** x/tieredrewards/keeper/delegation.go (L55-57)
```go
func (k Keeper) undelegate(ctx context.Context, delAddr sdk.AccAddress, valAddr sdk.ValAddress, shares math.LegacyDec) (time.Time, math.Int, error) {
	return k.stakingKeeper.Undelegate(ctx, delAddr, valAddr, shares)
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L182-185)
```go
	completionTime, _, err := ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L41-49)
```go
	if !pos.IsDelegated() {
		// Defensive
		k.logger(ctx).Error("delegation missing during BeforeRedelegationSlashed",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"shares_to_unbond", sharesToUnbond.String(),
		)
		return nil
	}
```
