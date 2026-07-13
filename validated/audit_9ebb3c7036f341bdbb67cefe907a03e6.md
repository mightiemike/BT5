Looking at the `ExitTierWithDelegation` partial-exit path in `x/tieredrewards/keeper/msg_server.go`, I can identify a concrete analog to H-02.

---

### Title
Partial `ExitTierWithDelegation` Does Not Decrement `pos.Delegation.Shares`, Leaving Position With Inflated Share Count - (File: `x/tieredrewards/keeper/msg_server.go`)

### Summary
When a user calls `ExitTierWithDelegation` with a partial amount (not a full exit), the function correctly unbonds `unbondedShares` from the position's delegator address and re-delegates them to the owner. However, it computes `remainingShares` but never writes it back to `pos.Delegation.Shares` before calling `setPosition`. The position is persisted with the original (inflated) share count, desynchronizing the on-chain position record from the actual staking delegation.

### Finding Description

In `ExitTierWithDelegation`, the partial-exit branch (the `else` block) computes `remainingShares` for a minimum-lock check but never assigns it back to the position:

```go
// x/tieredrewards/keeper/msg_server.go  lines 582-602
} else {
    remainingShares := pos.Delegation.Shares.Sub(unbondedShares)
    remainingPositionAmount, err := ms.reconcileAmountFromShares(ctx, valAddr, remainingShares)
    ...
    if !tier.MeetsMinLockRequirement(remainingPositionAmount) { ... }

    if err := ms.setPosition(ctx, pos.Position, nil); err != nil {   // ← pos.Delegation.Shares is NEVER updated
        return nil, err
    }
}
``` [1](#0-0) 

The actual staking module state is correct: `posDelAddr` now holds only `remainingShares` shares. But the persisted `pos.Delegation.Shares` still equals the pre-transfer value. The missing assignment is:

```go
pos.Delegation.Shares = remainingShares   // never executed
```

Compare with the full-exit path, which deletes the position entirely and sweeps any residual bank balance — there is no analogous "update shares" step for the partial path. [2](#0-1) 

The `transferDelegationFromPosition` helper correctly unbonds `unbondedShares` and re-delegates `transferredAmount` to the owner, but it only returns the values — it does not mutate `pos`: [3](#0-2) 

### Impact Explanation

After a partial `ExitTierWithDelegation`, `pos.Delegation.Shares` is inflated relative to the actual delegation at `posDelAddr`. This causes two concrete harms:

1. **Inflated bonus reward claims.** `processEventsAndClaimBonus` uses `pos.Delegation.Shares` (via `computeSegmentBonus`) to compute the bonus owed. With an inflated share count, every subsequent `ClaimTierRewards` call overpays bonus from the shared `RewardsPoolName` module account, draining it at the expense of other users. [4](#0-3) 

2. **Subsequent operations fail, locking the remaining delegation.** `TierUndelegate` passes `pos.Delegation.Shares` directly to `ms.undelegate`, which calls `stakingKeeper.Undelegate` with more shares than `posDelAddr` actually holds. The staking module rejects this, permanently blocking the user from undelegating their remaining position. [5](#0-4) 

### Likelihood Explanation

Any user who calls `ExitTierWithDelegation` with a partial amount triggers the bug. The `MsgExitTierWithDelegation` message accepts an arbitrary `amount` field and the only guard is the minimum-lock check on the *remaining* amount. A user with a position above twice the tier minimum can trivially trigger a partial exit. The integration test `test_exit_tier_with_delegation_partial` exercises this path but only asserts `int(pos_after["amount"]) < amount`, not that the stored shares equal the expected remainder, so the bug is not caught by existing tests. [6](#0-5) 

### Recommendation

In the partial-exit branch, assign `remainingShares` back to the position before persisting it:

```go
} else {
    remainingShares := pos.Delegation.Shares.Sub(unbondedShares)
    pos.Delegation.Shares = remainingShares   // ← add this line
    remainingPositionAmount, err := ms.reconcileAmountFromShares(ctx, valAddr, remainingShares)
    ...
    if err := ms.setPosition(ctx, pos.Position, nil); err != nil {
        return nil, err
    }
}
```

Add a unit test that verifies `pos.Delegation.Shares` after a partial exit equals the pre-exit shares minus the unbonded shares, and that a subsequent `TierUndelegate` succeeds.

### Proof of Concept

1. Alice locks 2 × `tier.MinLockAmount` tokens into a tier position on validator V. Position records `S` shares.
2. Alice calls `ExitTierWithDelegation` with `amount = tier.MinLockAmount` (partial exit, leaving exactly the minimum).
3. `transferDelegationFromPosition` unbonds `U` shares from `posDelAddr` and re-delegates to Alice. `posDelAddr` now holds `S - U` shares.
4. The partial-exit branch computes `remainingShares = S - U` for the min-lock check, but saves the position with `Delegation.Shares = S` (original).
5. Alice calls `ClaimTierRewards`. `processEventsAndClaimBonus` computes bonus using `S` shares instead of `S - U` → Alice receives `S/(S-U)` times the correct bonus, draining the rewards pool.
6. Alice repeats step 5 indefinitely, each time claiming inflated bonus rewards.
7. If Alice later calls `TierUndelegate`, the staking module rejects the call because `posDelAddr` only holds `S - U` shares but the position requests `S` shares → Alice's remaining delegation is permanently locked. [1](#0-0) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L182-183)
```go
	completionTime, _, err := ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)
	if err != nil {
```

**File:** x/tieredrewards/keeper/msg_server.go (L548-557)
```go
	transferredShares, unbondedShares, transferredAmount, err := ms.transferDelegationFromPosition(ctx, pos, valAddr, msg.Amount)
	if err != nil {
		return nil, err
	}

	// Capture for event before potential deletion.
	posId := pos.Id
	tierId := pos.TierId

	fullExit := pos.ExitWithFullDelegation(msg.Amount, positionAmount)
```

**File:** x/tieredrewards/keeper/msg_server.go (L559-580)
```go
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
```

**File:** x/tieredrewards/keeper/msg_server.go (L582-602)
```go
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
```

**File:** x/tieredrewards/keeper/transfer_delegation.go (L141-169)
```go
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L87-103)
```go
func (k Keeper) claimRewards(ctx context.Context, pos types.PositionState) (types.PositionState, sdk.Coins, sdk.Coins, error) {
	if !pos.IsDelegated() {
		return pos, sdk.NewCoins(), sdk.NewCoins(), nil
	}

	base, err := k.claimBaseRewards(ctx, pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	return pos, base, bonus, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-213)
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
