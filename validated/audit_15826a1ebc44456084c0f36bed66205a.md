### Title
Migration Halt: `exitVestedAccountsPositions` Aborts v8 Upgrade When Vesting Position's Validator Is Jailed — (`x/tieredrewards/migrations/v2/migrate.go`)

---

### Summary

The v2 migration's `exitVestedAccountsPositions` calls `ForceFullExitWithDelegation` for every vesting-account-owned position. That function calls `transferDelegationFromPosition`, which contains a hard `IsBonded()` guard that returns `ErrValidatorNotBonded` for any jailed or unbonding validator. The error is not caught or skipped anywhere in the migration call stack — it propagates all the way back through `Migrate1to2` → `RunMigrations`, halting the v8 upgrade entirely.

---

### Finding Description

**Call chain:**

```
Migrate1to2 (migrations.go:17)
  → v2.Migrate (v2/migrate.go:28)
    → exitVestedAccountsPositions (v2/migrate.go:37)
      → pk.ForceFullExitWithDelegation(ctx, posID) (v2/migrate.go:99)
        → k.transferDelegationFromPosition(...) (force_exit.go:62)
          → validator.IsBonded() == false → ErrValidatorNotBonded (transfer_delegation.go:132-134)
```

**Root cause — `transferDelegationFromPosition`:** [1](#0-0) 

This guard is unconditional. A jailed validator has status `Unbonding`, not `Bonded`, so `IsBonded()` returns `false` and the function returns an error.

**Error propagation — `ForceFullExitWithDelegation`:** [2](#0-1) 

No error suppression; the error is wrapped and returned.

**Error propagation — `exitVestedAccountsPositions`:** [3](#0-2) 

No error suppression; the error is wrapped and returned.

**Error propagation — `Migrate`:** [4](#0-3) 

**Error propagation — `Migrate1to2`:** [5](#0-4) 

At no point in this chain is the error from a jailed validator caught, logged-and-skipped, or otherwise handled gracefully.

---

### Impact Explanation

If any vesting account owns a tier position delegated to a validator that is jailed (or in unbonding status) at the moment the v8 upgrade block is executed:

1. `RunMigrations` returns a non-nil error.
2. The upgrade handler panics or returns an error, halting the chain at the upgrade height.
3. All funds locked in tier positions remain locked indefinitely until the chain is patched and restarted via a coordinated emergency upgrade.

This is a **Critical** impact: chain upgrade halt with funds locked in positions.

---

### Likelihood Explanation

Validator jailing is a routine on-chain event (downtime slashing, double-sign evidence). The window between the governance upgrade proposal passing and the upgrade height executing can be days or weeks — ample time for a validator to be jailed. Any single vesting-account-owned position on a jailed validator is sufficient to trigger the halt. The precondition requires no attacker action; it can occur naturally.

---

### Recommendation

In `exitVestedAccountsPositions` (or inside `ForceFullExitWithDelegation`), detect the `ErrValidatorNotBonded` case and handle it gracefully instead of propagating the error. Options:

1. **Skip and log**: If `ForceFullExitWithDelegation` returns `ErrValidatorNotBonded`, log a warning and continue to the next position. The position can be exited by the owner after the upgrade once the validator re-bonds or via a separate cleanup path.
2. **Initiate a standard unbonding**: Instead of the transfer-delegation path, call `staking.Undelegate` directly on the position's delegator address for jailed/unbonding validators, which does not require the validator to be bonded.
3. **Mark position for deferred exit**: Record the position ID in a migration-specific store and handle it in a post-upgrade ABCI hook.

Option 1 is the safest minimal fix: it prevents the upgrade halt while leaving the position in a state the owner can still exit.

---

### Proof of Concept

1. Create a vesting account `V` and have it open a tier position delegated to validator `val1`.
2. Jail `val1` (e.g., via `slashingKeeper.Jail`).
3. Run `Migrate1to2` (or trigger the v8 upgrade handler).
4. Observe: `exitVestedAccountsPositions` calls `ForceFullExitWithDelegation` for `V`'s position → `transferDelegationFromPosition` returns `ErrValidatorNotBonded` → `Migrate1to2` returns a non-nil error → upgrade halts.

The exact assertion: `require.NoError(t, migrator.Migrate1to2(ctx))` fails with `"force-exit position N: transfer delegation back to owner for position N: validator not bonded"`.

### Citations

**File:** x/tieredrewards/keeper/transfer_delegation.go (L132-134)
```go
	if !validator.IsBonded() {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, types.ErrValidatorNotBonded
	}
```

**File:** x/tieredrewards/keeper/force_exit.go (L62-64)
```go
	if _, _, _, err := k.transferDelegationFromPosition(ctx, posState, valAddr, positionAmount); err != nil {
		return fmt.Errorf("transfer delegation back to owner for position %d: %w", posID, err)
	}
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L37-40)
```go
	if err := exitVestedAccountsPositions(ctx, positions, ak, pk); err != nil {
		return fmt.Errorf("exit vested accounts positions: %w", err)
	}
	return nil
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L97-101)
```go
	for _, posID := range toExit {
		sdkCtx.Logger().Info("v8 migration: force-exit vesting-owned position", "position_id", posID)
		if err := pk.ForceFullExitWithDelegation(ctx, posID); err != nil {
			return fmt.Errorf("force-exit position %d: %w", posID, err)
		}
```

**File:** x/tieredrewards/keeper/migrations.go (L17-19)
```go
func (m Migrator) Migrate1to2(ctx sdk.Context) error {
	return v2.Migrate(ctx, m.keeper.Positions, m.keeper.accountKeeper, m.keeper)
}
```
