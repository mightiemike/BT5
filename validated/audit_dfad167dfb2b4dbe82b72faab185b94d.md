### Title
`ExitTierWithDelegation` Saves Stale Delegation Shares After Partial Exit — (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

In `MsgExitTierWithDelegation`, after `transferDelegationFromPosition` unbonds a portion of the position's shares from the staking module, the in-memory `pos.Delegation.Shares` field is never updated to reflect the reduced delegation. The partial-exit branch then calls `setPosition(ctx, pos.Position, nil)` with the original, pre-exit share count. Every subsequent operation that reads `pos.Delegation.Shares` from storage — `TierUndelegate`, `TierRedelegate`, and a follow-up full `ExitTierWithDelegation` — will attempt to operate on more shares than actually exist, causing those calls to fail and permanently locking the user's remaining delegation.

---

### Finding Description

`ExitTierWithDelegation` in `msg_server.go` follows this sequence:

1. Load position state (`getPositionState`).
2. Claim rewards — `pos` is updated and returned.
3. Compute `positionAmount` from `pos.Delegation.Shares` (pre-exit value).
4. Call `transferDelegationFromPosition(ctx, pos, valAddr, msg.Amount)` — this unbonds `unbondedShares` from `pos.DelegatorAddress` and re-delegates `transferredAmount` to `pos.Owner`. **`pos` is passed by value; the function does not return an updated `pos`.**
5. Determine `fullExit := pos.ExitWithFullDelegation(msg.Amount, positionAmount)`.
6. In the **partial-exit branch** (`else`), compute `remainingShares := pos.Delegation.Shares.Sub(unbondedShares)` — this is the correct remaining share count — but then call `setPosition(ctx, pos.Position, nil)` **without first writing `remainingShares` back into `pos`**. [1](#0-0) 

The result is that `pos.Position` (and therefore `pos.Delegation.Shares`) stored on-chain still holds the original pre-exit share count, while the actual staking-module delegation for `pos.DelegatorAddress` has been reduced by `unbondedShares`.

The correct remaining shares are computed locally at line 583 but are never written back: [2](#0-1) 

---

### Impact Explanation

After a partial `ExitTierWithDelegation`, the stored position has `Delegation.Shares = S_original`, but the actual on-chain delegation for `pos.DelegatorAddress` is `S_original − unbondedShares`.

Every subsequent operation that reads the stored shares and passes them directly to the staking keeper will fail:

- **`TierUndelegate`** calls `ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)` — the stale (too-high) share count exceeds the actual delegation; the staking keeper rejects it. [3](#0-2) 

- **`TierRedelegate`** calls `ms.redelegate(ctx, delAddr, srcValAddr, dstValAddr, pos.Delegation.Shares)` — same failure. [4](#0-3) 

- A follow-up **full `ExitTierWithDelegation`** passes `pos.Delegation.Shares` as `unbondedShares` inside `transferDelegationFromPosition`, which calls `Unbond` with the stale count — rejected. [5](#0-4) 

The user's remaining delegation is permanently locked inside the position: they cannot undelegate, redelegate, or fully exit. The locked tokens continue to accrue rewards but are inaccessible.

---

### Likelihood Explanation

Any user who calls `MsgExitTierWithDelegation` with an amount smaller than their full position triggers the bug. This is a normal, documented operation (partial exit). No special privileges, leaked keys, or unusual configuration are required. The entry path is a standard signed transaction.

---

### Recommendation

Before calling `setPosition` in the partial-exit branch, update `pos.Delegation.Shares` (or the equivalent field inside `pos.Position`) to `remainingShares`:

```go
} else {
    remainingShares := pos.Delegation.Shares.Sub(unbondedShares)
    remainingPositionAmount, err := ms.reconcileAmountFromShares(ctx, valAddr, remainingShares)
    if err != nil {
        return nil, err
    }
    // ... min-lock check ...

+   pos.Delegation.Shares = remainingShares   // update stored shares to post-exit value

    if err := ms.setPosition(ctx, pos.Position, nil); err != nil {
        return nil, err
    }
}
```

This mirrors the fix in the external report: use the source's (position's post-transfer) value rather than the pre-transfer value when persisting state.

---

### Proof of Concept

1. Alice creates a position with 1000 tokens delegated to validator V. Stored `pos.Delegation.Shares = S` (corresponding to 1000 tokens).
2. Alice calls `MsgExitTierWithDelegation` with `Amount = 400` (partial exit).
   - `transferDelegationFromPosition` unbonds shares worth 400 tokens from `pos.DelegatorAddress` and re-delegates to Alice's owner address.
   - `unbondedShares ≈ 0.4 * S`.
   - `remainingShares ≈ 0.6 * S` — computed but never written back.
   - `setPosition` saves the position with `Delegation.Shares = S` (stale).
3. Alice calls `MsgTierUndelegate` on the same position.
   - `pos.Delegation.Shares = S` is loaded from storage.
   - `ms.undelegate(ctx, delAddr, valAddr, S)` is called.
   - The staking keeper finds only `0.6 * S` shares for `delAddr` → returns an insufficient-shares error.
   - Alice's remaining 600-token delegation is permanently locked. [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L182-183)
```go
	completionTime, _, err := ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)
	if err != nil {
```

**File:** x/tieredrewards/keeper/msg_server.go (L245-245)
```go
	completionTime, unbondingID, err := ms.redelegate(ctx, delAddr, srcValAddr, dstValAddr, pos.Delegation.Shares)
```

**File:** x/tieredrewards/keeper/msg_server.go (L518-603)
```go
func (ms msgServer) ExitTierWithDelegation(ctx context.Context, msg *types.MsgExitTierWithDelegation) (*types.MsgExitTierWithDelegationResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateExitTierWithDelegation(ctx, pos, msg.Owner, msg.Amount); err != nil {
		return nil, err
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}

	validator := pos.Delegation.ValidatorAddress
	valAddr, err := sdk.ValAddressFromBech32(validator)
	if err != nil {
		return nil, err
	}

	positionAmount, err := ms.reconcileAmountFromShares(ctx, valAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
	}

	transferredShares, unbondedShares, transferredAmount, err := ms.transferDelegationFromPosition(ctx, pos, valAddr, msg.Amount)
	if err != nil {
		return nil, err
	}

	// Capture for event before potential deletion.
	posId := pos.Id
	tierId := pos.TierId

	fullExit := pos.ExitWithFullDelegation(msg.Amount, positionAmount)

	if fullExit {
		ownerAddr, err := sdk.AccAddressFromBech32(msg.Owner)
		if err != nil {
			return nil, err
		}

		delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
		if err != nil {
			return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
		}

		// sweeps any remaining dust if any (usually zero)
		balances := ms.bankKeeper.SpendableCoins(ctx, delAddr)
		if !balances.IsZero() {
			if err := ms.bankKeeper.SendCoins(ctx, delAddr, ownerAddr, balances); err != nil {
				return nil, err
			}
		}

		if err := ms.deletePosition(ctx, pos.Position, &ValidatorTransition{PreviousAddress: validator}); err != nil {
			return nil, err
		}

	} else {
		remainingShares := pos.Delegation.Shares.Sub(unbondedShares)
		// Compute remaining token value for min lock check.
		remainingPositionAmount, err := ms.reconcileAmountFromShares(ctx, valAddr, remainingShares)
		if err != nil {
			return nil, err
		}

		tier, err := ms.getTier(ctx, pos.TierId)
		if err != nil {
			return nil, err
		}
		// actual remaining amount (post-transfer) must meet min lock.
		if !tier.MeetsMinLockRequirement(remainingPositionAmount) {
			return nil, errorsmod.Wrapf(types.ErrMinLockAmountNotMet,
				"remaining amount %s is below tier minimum %s", remainingPositionAmount, tier.MinLockAmount)
		}

		if err := ms.setPosition(ctx, pos.Position, nil); err != nil {
			return nil, err
		}
	}
```

**File:** x/tieredrewards/keeper/transfer_delegation.go (L100-169)
```go
func (k Keeper) transferDelegationFromPosition(ctx context.Context, pos types.PositionState, valAddr sdk.ValAddress, amount math.Int) (math.LegacyDec, math.LegacyDec, math.Int, error) {
	owner, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	posDelAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	// Defensive
	if !pos.IsDelegated() {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, errorsmod.Wrapf(types.ErrPositionNotDelegated, "position %d is not delegated", pos.Id)
	}

	// Defensive
	isRedelegating, err := k.isRedelegating(ctx, pos.DelegatorAddress)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}
	if isRedelegating {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, errorsmod.Wrapf(types.ErrActiveRedelegation, "position %d has an active redelegation", pos.Id)
	}

	validator, err := k.stakingKeeper.GetValidator(ctx, valAddr)
	if errors.Is(err, stakingtypes.ErrNoValidatorFound) {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, types.ErrTransferDelegationDestNotFound
	} else if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}

	if !validator.IsBonded() {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, types.ErrValidatorNotBonded
	}

	positionAmount, err := k.reconcileAmountFromShares(ctx, valAddr, pos.Delegation.Shares)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}

	unbondedShares := pos.Delegation.Shares
	if !pos.ExitWithFullDelegation(amount, positionAmount) {
		unbondedShares, err = k.stakingKeeper.ValidateUnbondAmount(ctx, posDelAddr, valAddr, amount)
		if err != nil {
			return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
		}
	}

	transferredAmount, err := k.stakingKeeper.Unbond(ctx, posDelAddr, valAddr, unbondedShares)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}

	if transferredAmount.IsZero() {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, types.ErrTinyTransferDelegationAmount
	}

	// Re-fetch updated validator
	validator, err = k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}

	ownerNewShares, err := k.stakingKeeper.Delegate(ctx, owner, transferredAmount, validator.GetStatus(), validator, false)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}

	return ownerNewShares, unbondedShares, transferredAmount, nil
```
