Looking at the exact code path described, I need to trace through `ForceFullExitWithDelegation` → `reconcileAmountFromShares` → `transferDelegationFromPosition` → `ExitWithFullDelegation` → `Unbond` → `ErrTinyTransferDelegationAmount`.

**Step 1: `reconcileAmountFromShares` returns 0** [1](#0-0) 

When a validator has been slashed such that `TokensFromShares(shares).TruncateInt() == 0` (i.e., the position holds few shares and the token-per-share ratio has dropped below 1), this function returns `math.ZeroInt()`.

**Step 2: `ForceFullExitWithDelegation` passes `positionAmount = 0` to `transferDelegationFromPosition`** [2](#0-1) 

**Step 3: Inside `transferDelegationFromPosition`, `ExitWithFullDelegation(0, 0)` returns `true`**

`ExitWithFullDelegation` is simply `amount.Equal(positionAmount)`: [3](#0-2) 

When both `amount` and `positionAmount` are 0, `0.Equal(0) == true`, so the full-delegation path is taken: `unbondedShares = pos.Delegation.Shares` (all shares).

**Step 4: `Unbond(allShares)` returns `transferredAmount = 0`, triggering `ErrTinyTransferDelegationAmount`** [4](#0-3) 

The Cosmos SDK `Unbond` computes `TokensFromShares(shares).TruncateInt()`. For a heavily slashed validator, this truncates to 0, and the guard at line 154 returns `ErrTinyTransferDelegationAmount`.

**Step 5: Migration halts — no recovery path**

The migration iterates all vesting-owned positions and calls `ForceFullExitWithDelegation` for each. Any error causes the entire migration to abort: [5](#0-4) 

There is no `continue`-on-error logic; a single failing position halts the entire upgrade handler.

**Is the precondition realistic?**

The validator does **not** need to be tombstoned. The `IsBonded()` check at line 132 of `transfer_delegation.go` would reject tombstoned (jailed/unbonded) validators with `ErrValidatorNotBonded` — a different error. The described path requires a validator that is still **bonded** but whose token-per-share ratio has dropped enough that the position's shares truncate to 0 tokens. This can occur through repeated downtime slashing (which does not tombstone) or any slash event that reduces the ratio below 1 token per share for the position's share count. No attacker is required — this is a natural chain state.

---

### Title
Migration halt via `ErrTinyTransferDelegationAmount` for vesting positions on heavily-slashed bonded validators — (`x/tieredrewards/keeper/force_exit.go`, `transfer_delegation.go`)

### Summary
`ForceFullExitWithDelegation` calls `reconcileAmountFromShares`, which returns 0 when a bonded validator's token-per-share ratio has been reduced by slashing such that the position's shares map to < 1 token. This 0 is passed to `transferDelegationFromPosition`, where `ExitWithFullDelegation(0, 0)` evaluates to `true` (both sides equal), causing `Unbond` to be called with all shares. `Unbond` also returns 0 tokens, triggering `ErrTinyTransferDelegationAmount`. The v2 migration has no error-skip logic, so this halts the entire upgrade for all subsequent vesting-owned positions.

### Finding Description
The bug is a missing zero-amount guard in `ForceFullExitWithDelegation`. When `positionAmount == 0`, the function should either skip the delegation transfer (there is nothing to return to the owner) or handle the zero case gracefully. Instead, it passes 0 to `transferDelegationFromPosition`, which interprets `ExitWithFullDelegation(0, 0) == true` as "exit with all shares," unbonds them, receives 0 tokens back, and returns `ErrTinyTransferDelegationAmount`. This error is not caught or skipped by the migration loop.

The `transferDelegationFromPosition` function also independently recalculates `positionAmount` via `reconcileAmountFromShares` at line 136, so the result is the same regardless of what the caller passes. [6](#0-5) 

### Impact Explanation
The v8 upgrade migration (`Migrate1to2` → `exitVestedAccountsPositions`) halts entirely if any vesting-owned position is delegated to a bonded validator whose token-per-share ratio has dropped below 1 token per position share. All subsequent vesting-owned positions in the iteration are not processed. The upgrade handler returns an error, blocking the chain upgrade.

### Likelihood Explanation
Any bonded validator that has been slashed (downtime or equivocation before tombstoning) and whose token-per-share ratio has dropped sufficiently can trigger this. Positions with small share counts are especially susceptible. No attacker is required; this is a natural chain state reachable through normal slashing events. A malicious validator operator could also deliberately trigger this before the upgrade.

### Recommendation
In `ForceFullExitWithDelegation`, add an explicit guard after `reconcileAmountFromShares`:

```go
if positionAmount.IsZero() {
    // Nothing to transfer back; position's value was fully slashed away.
    // Skip the delegation transfer and proceed to delete the position.
    logger.Warn("force-exit: position amount is zero after slashing, skipping delegation transfer", ...)
} else {
    if _, _, _, err := k.transferDelegationFromPosition(...); err != nil {
        return ...
    }
}
```

Similarly, `transferDelegationFromPosition` should guard against `positionAmount == 0` before calling `Unbond`.

### Proof of Concept
1. Set up a keeper test with a vesting account owning a tieredrewards position delegated to validator V.
2. Slash validator V via `slashValidatorDirect` to reduce its token-per-share ratio below 1 token per position share (e.g., slash 99% with a small position).
3. Confirm validator V remains bonded (not jailed/tombstoned).
4. Call `ForceFullExitWithDelegation(ctx, posID)`.
5. Assert the returned error wraps `ErrTinyTransferDelegationAmount`.
6. Confirm the migration loop in `exitVestedAccountsPositions` would halt at this position.

### Citations

**File:** x/tieredrewards/keeper/delegation.go (L31-40)
```go
func (k Keeper) reconcileAmountFromShares(ctx context.Context, valAddr sdk.ValAddress, shares math.LegacyDec) (math.Int, error) {
	val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return math.Int{}, err
	}
	if val.GetDelegatorShares().IsZero() {
		return math.ZeroInt(), nil
	}
	return val.TokensFromShares(shares).TruncateInt(), nil
}
```

**File:** x/tieredrewards/keeper/force_exit.go (L52-64)
```go
	positionAmount, err := k.reconcileAmountFromShares(ctx, valAddr, posState.Delegation.Shares)
	if err != nil {
		return fmt.Errorf("reconcile amount for position %d: %w", posID, err)
	}
	logger.Info("force-exit: reconciled position amount",
		"position_id", posID,
		"amount", positionAmount.String(),
		"validator", valAddr.String(),
	)

	if _, _, _, err := k.transferDelegationFromPosition(ctx, posState, valAddr, positionAmount); err != nil {
		return fmt.Errorf("transfer delegation back to owner for position %d: %w", posID, err)
	}
```

**File:** x/tieredrewards/types/position.go (L94-96)
```go
func (p Position) ExitWithFullDelegation(amount, positionAmount math.Int) bool {
	return amount.Equal(positionAmount)
}
```

**File:** x/tieredrewards/keeper/transfer_delegation.go (L136-156)
```go
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
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L97-101)
```go
	for _, posID := range toExit {
		sdkCtx.Logger().Info("v8 migration: force-exit vesting-owned position", "position_id", posID)
		if err := pk.ForceFullExitWithDelegation(ctx, posID); err != nil {
			return fmt.Errorf("force-exit position %d: %w", posID, err)
		}
```
