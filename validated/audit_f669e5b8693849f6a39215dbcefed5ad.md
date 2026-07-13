### Title
WithdrawFromTier Deletes Position Before Unbonding Completes, Permanently Locking Delegated Funds - (File: x/tieredrewards/keeper/msg_server.go)

### Summary

`WithdrawFromTier` in the tiered-rewards module deletes the position record unconditionally, even when the delegator sub-account (`delAddr`) has a zero spendable balance because the Cosmos SDK unbonding period has not yet elapsed. Once the position is deleted, the funds that eventually arrive in `delAddr` after unbonding completes have no associated position record and cannot be recovered by the owner.

### Finding Description

The `WithdrawFromTier` message handler follows this sequence:

1. Reads spendable coins from the position's delegator sub-account (`delAddr`).
2. Conditionally sends those coins to the owner **only if the balance is non-zero**.
3. **Unconditionally** deletes the position record. [1](#0-0) 

```go
balances := ms.bankKeeper.SpendableCoins(ctx, delAddr)
if !balances.IsZero() {
    if err := ms.bankKeeper.SendCoins(ctx, delAddr, ownerAddr, balances); err != nil {
        return nil, err
    }
}

if err := ms.deletePosition(ctx, pos.Position, nil); err != nil {
    return nil, err
}
```

The normal exit flow is:

1. `LockTier` / `CommitDelegationToTier` — creates position, delegates funds via a position-specific `delAddr`.
2. `TriggerExitFromTier` — sets the exit timer.
3. `TierUndelegate` — undelegates all shares; funds enter the Cosmos SDK 21-day unbonding queue. The position's delegation shares become zero and the position is saved.
4. `WithdrawFromTier` — **intended** to be called after unbonding completes and funds have landed in `delAddr`. [2](#0-1) 

After step 3, the position has zero delegation shares and a triggered exit, so `validateWithdrawFromTier` will pass. However, `delAddr` has zero spendable balance because the unbonding has not completed. The function skips the fund transfer (the `if !balances.IsZero()` branch is not taken) and proceeds to delete the position. After the 21-day unbonding period, the Cosmos SDK releases the tokens into `delAddr`, but the position record no longer exists. There is no message or recovery path that allows the owner to claim funds from a deleted position's sub-account.

The `delAddr` is a deterministic sub-account created per position by `createPositionDelegatorAccount`: [3](#0-2) 

Once the position is deleted, the owner has no protocol-level handle to that address.

### Impact Explanation

The owner's entire locked principal is permanently stranded in the position's `delAddr` sub-account. The position record is gone, so no tiered-rewards message can reference it. The funds are not burned — they exist on-chain — but they are inaccessible to the owner and to the protocol. This is a direct, irreversible loss of user funds for any position where `WithdrawFromTier` is called before the unbonding period elapses.

### Likelihood Explanation

The trigger is a normal, owner-signed `MsgWithdrawFromTier` transaction submitted too early (e.g., immediately after `MsgTierUndelegate`). No privileged role is required. A user who misunderstands the unbonding timeline, or who is front-run by a relayer replaying a pre-signed transaction, can hit this path. The 21-day unbonding window is a long period during which the mistake can occur.

### Recommendation

Add an explicit check in `WithdrawFromTier` (or in `validateWithdrawFromTier`) that the delegator sub-account has no outstanding unbonding delegations before allowing the position to be deleted. Concretely:

- Query `stakingKeeper.GetUnbondingDelegation(ctx, delAddr, valAddr)` and return an error if any unbonding entry is still pending.
- Alternatively, assert `!balances.IsZero()` as a hard error rather than a silent skip, so the transaction fails instead of silently deleting the position with zero funds transferred.

### Proof of Concept

1. Alice calls `MsgLockTier` with 10,000 CRO → position ID 42 is created, `delAddr42` holds the delegation.
2. Alice calls `MsgTriggerExitFromTier{PositionId: 42}` → exit timer set.
3. Alice calls `MsgTierUndelegate{PositionId: 42}` → 10,000 CRO enters the 21-day unbonding queue; `delAddr42` spendable balance = 0.
4. Alice (or anyone who can sign on her behalf) immediately calls `MsgWithdrawFromTier{PositionId: 42, Owner: alice}`.
5. `validateWithdrawFromTier` passes (zero shares, exit triggered, lock period satisfied).
6. `ms.bankKeeper.SpendableCoins(ctx, delAddr42)` returns `[]` (zero).
7. The `if !balances.IsZero()` branch is skipped — no coins sent to Alice.
8. `ms.deletePosition(ctx, pos.Position, nil)` executes — position 42 is erased from state.
9. 21 days later, the Cosmos SDK unbonding completes and deposits 10,000 CRO into `delAddr42`.
10. Alice has no position record and no message to recover the funds. The 10,000 CRO is permanently locked in `delAddr42`. [4](#0-3)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L52-55)
```go
	delAddr, err := ms.createPositionDelegatorAccount(ctx, ownerAddr, id)
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

**File:** x/tieredrewards/keeper/msg_server.go (L470-516)
```go
func (ms msgServer) WithdrawFromTier(ctx context.Context, msg *types.MsgWithdrawFromTier) (*types.MsgWithdrawFromTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateWithdrawFromTier(ctx, pos, msg.Owner); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	balances := ms.bankKeeper.SpendableCoins(ctx, delAddr)
	if !balances.IsZero() {
		if err := ms.bankKeeper.SendCoins(ctx, delAddr, ownerAddr, balances); err != nil {
			return nil, err
		}
	}

	if err := ms.deletePosition(ctx, pos.Position, nil); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionWithdrawn{
		Position: pos.Position,
		Amount:   balances,
	}); err != nil {
		return nil, err
	}

	return &types.MsgWithdrawFromTierResponse{
		Amount: balances,
	}, nil
}
```
