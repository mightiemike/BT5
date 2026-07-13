### Title
Migration `exitVestedAccountsPositions` Halts v8 Upgrade on Vesting-Owned Positions with Active Redelegations — (`x/tieredrewards/migrations/v2/migrate.go`)

---

### Summary

The v8 upgrade migration unconditionally calls `ForceFullExitWithDelegation` for every vesting-owned position. `ForceFullExitWithDelegation` calls `transferDelegationFromPosition`, which checks `isRedelegating` against the staking module's live redelegation records and returns `ErrActiveRedelegation` if the position's delegator address has an in-flight redelegation. This error propagates unhandled all the way back to the upgrade handler, causing a chain halt.

---

### Finding Description

**Entrypoint:** v8 upgrade handler → `RunMigrations` → `Migrate` → `exitVestedAccountsPositions`.

`exitVestedAccountsPositions` walks all positions, identifies those owned by vesting accounts, and calls `ForceFullExitWithDelegation` for each one with no tolerance for `ErrActiveRedelegation`: [1](#0-0) 

`ForceFullExitWithDelegation` calls `transferDelegationFromPosition` and propagates its error directly: [2](#0-1) 

`transferDelegationFromPosition` calls `isRedelegating` on the position's delegator address and returns `ErrActiveRedelegation` if any staking-module redelegation record exists for that address: [3](#0-2) 

`isRedelegating` queries the staking module directly — it is not a soft check against the `RedelegationMappings` index, it reflects live staking state: [4](#0-3) 

Redelegation is a fully supported, normal user operation for positions (confirmed by `msg_server.go` containing `Redelegate`/`setRedelegationMapping` calls and the existence of `msg_server_redelegate_test.go`). When a position redelegates, the staking module creates a redelegation entry for the position's per-position delegator address with the standard unbonding period (21 days on mainnet). During that window, `isRedelegating` returns `true`.

The `RedelegationMappings` store (unbonding_id → position_id) is the module's own secondary index tracking exactly these in-flight redelegations: [5](#0-4) 

`ErrActiveRedelegation` is a hard-registered module error, not a sentinel that is caught and swallowed anywhere in the migration path: [6](#0-5) 

---

### Impact Explanation

The error propagates without any catch or skip:

```
transferDelegationFromPosition → ErrActiveRedelegation
  ↑ ForceFullExitWithDelegation (line 63: returns fmt.Errorf("transfer delegation back to owner…: %w", err))
    ↑ exitVestedAccountsPositions (line 100: returns fmt.Errorf("force-exit position %d: %w", posID, err))
      ↑ Migrate (line 38: returns fmt.Errorf("exit vested accounts positions: %w", err))
        ↑ RunMigrations → upgrade handler → chain halt
```

State at halt: `backfillDelegatorAddress` has already completed (all positions have their `DelegatorAddress` backfilled), but some vesting-owned positions have been exited while others have not. The module is in a partially migrated state with orphaned delegations for the un-exited positions.

---

### Likelihood Explanation

The precondition is reachable through entirely normal user operations:
1. A vesting account creates a tieredrewards position (supported — vesting accounts can hold positions pre-v8).
2. The vesting account redelegates the position to a different validator.
3. The v8 upgrade fires within the 21-day redelegation unbonding window.

No governance, operator privilege, or key compromise is required. Any single such position on mainnet at upgrade time triggers the halt.

---

### Recommendation

In `exitVestedAccountsPositions`, detect `ErrActiveRedelegation` and either:
- **Skip** the position (log a warning; handle it via a follow-up governance action or a subsequent migration step), or
- **Force-complete** the redelegation by cancelling it at the staking layer before calling `transferDelegationFromPosition`.

The simplest safe fix is to check `errors.Is(err, types.ErrActiveRedelegation)` after the `ForceFullExitWithDelegation` call and continue rather than return, logging the skipped position for manual resolution.

---

### Proof of Concept

```go
// In a migration integration test:
// 1. Create a vesting account.
// 2. Create a tieredrewards position owned by that account.
// 3. Redelegate the position (srcVal → dstVal) so the staking module
//    records an active redelegation for pos.DelegatorAddress.
// 4. Call migrations/v2.Migrate(ctx, positions, ak, keeper).
// 5. Assert: Migrate returns an error wrapping ErrActiveRedelegation.
//    The upgrade handler would therefore return an error, halting the chain.
```

The `RedelegationMappings` index confirms the position is redelegating; `isRedelegating` confirms the staking module agrees; `transferDelegationFromPosition` returns the error; the migration has no handler for it. [7](#0-6) [8](#0-7)

### Citations

**File:** x/tieredrewards/migrations/v2/migrate.go (L71-105)
```go
func exitVestedAccountsPositions(
	ctx context.Context,
	positions collections.Map[uint64, types.Position],
	ak AccountKeeper,
	pk PositionForceExiter,
) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	var toExit []uint64
	if err := positions.Walk(ctx, nil, func(posID uint64, pos types.Position) (bool, error) {
		ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
		if err != nil {
			return false, fmt.Errorf("parse owner address for position %d: %w", posID, err)
		}
		acc := ak.GetAccount(ctx, ownerAddr)
		if acc == nil {
			return false, fmt.Errorf("owner account not found for position %d: %s", posID, ownerAddr.String())
		}
		if _, ok := acc.(sdkvesting.VestingAccount); ok {
			toExit = append(toExit, posID)
		}
		return false, nil
	}); err != nil {
		return fmt.Errorf("walk positions: %w", err)
	}

	for _, posID := range toExit {
		sdkCtx.Logger().Info("v8 migration: force-exit vesting-owned position", "position_id", posID)
		if err := pk.ForceFullExitWithDelegation(ctx, posID); err != nil {
			return fmt.Errorf("force-exit position %d: %w", posID, err)
		}
	}

	sdkCtx.Logger().Info("v8 migration: vesting-owned positions exited", "count", len(toExit))
	return nil
```

**File:** x/tieredrewards/keeper/force_exit.go (L62-64)
```go
	if _, _, _, err := k.transferDelegationFromPosition(ctx, posState, valAddr, positionAmount); err != nil {
		return fmt.Errorf("transfer delegation back to owner for position %d: %w", posID, err)
	}
```

**File:** x/tieredrewards/keeper/transfer_delegation.go (L100-123)
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
```

**File:** x/tieredrewards/keeper/delegation.go (L89-99)
```go
func (k Keeper) isRedelegating(ctx context.Context, delegatorAddress string) (bool, error) {
	delAddr, err := sdk.AccAddressFromBech32(delegatorAddress)
	if err != nil {
		return false, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}
	reds, err := k.stakingKeeper.GetRedelegations(ctx, delAddr, 1)
	if err != nil {
		return false, err
	}
	return len(reds) > 0, nil
}
```

**File:** x/tieredrewards/keeper/redelegation_mapping.go (L39-41)
```go
func (k Keeper) setRedelegationMapping(ctx context.Context, unbondingId, positionId uint64) error {
	return k.RedelegationMappings.Set(ctx, unbondingId, positionId)
}
```

**File:** x/tieredrewards/types/errors.go (L15-15)
```go
	ErrActiveRedelegation               = errors.Register(ModuleName, 10, "cannot transfer delegation with active incoming redelegation")
```
