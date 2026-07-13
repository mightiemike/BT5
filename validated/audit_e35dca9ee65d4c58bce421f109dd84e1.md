Based on my analysis of the full call chain, this is a real, concrete vulnerability.

---

### Title
Migration Halt via Active Redelegation on Vesting-Owned Position — (`x/tieredrewards/migrations/v2/migrate.go`, `x/tieredrewards/keeper/transfer_delegation.go`)

### Summary

The v8 `Migrate1to2` migration force-exits all vesting-owned positions via `ForceFullExitWithDelegation`. That function calls `transferDelegationFromPosition`, which contains a hard guard: if the position's delegator address has an active redelegation, it returns `ErrActiveRedelegation`. This error propagates unconditionally up through `exitVestedAccountsPositions` → `Migrate` → `Migrate1to2`, halting the upgrade migration entirely.

### Finding Description

The call chain is:

```
Migrate1to2 (migrations.go:17)
  → v2.Migrate (v2/migrate.go:28)
    → exitVestedAccountsPositions (v2/migrate.go:71)
      → ForceFullExitWithDelegation (force_exit.go:14)
        → transferDelegationFromPosition (transfer_delegation.go:100)
          → isRedelegating(ctx, pos.DelegatorAddress) (delegation.go:89)
            → returns true
          → returns ErrActiveRedelegation (transfer_delegation.go:122)
        → returns error (force_exit.go:63)
      → returns error (v2/migrate.go:100)
    → returns error (v2/migrate.go:38)
  → Migrate1to2 returns error
```

**Step 1 — Attacker entry point:** A vesting account owner calls `TierRedelegate` (msg_server.go:210) on their position before the v8 upgrade. This calls `ms.redelegate(ctx, delAddr, srcValAddr, dstValAddr, pos.Delegation.Shares)` where `delAddr` is the position's module-derived delegator address. [1](#0-0) 

**Step 2 — Redelegation recorded on position's delegator address:** `BeginRedelegation` creates a staking redelegation entry keyed to `pos.DelegatorAddress` (the position's own module account), not the owner. The redelegation has a completion time set by the staking module's unbonding period. [2](#0-1) 

**Step 3 — Migration triggers the guard:** At upgrade time, `exitVestedAccountsPositions` iterates all positions owned by vesting accounts and calls `ForceFullExitWithDelegation` for each. [3](#0-2) 

**Step 4 — Hard block in `transferDelegationFromPosition`:** The function checks `isRedelegating(ctx, pos.DelegatorAddress)`. `isRedelegating` calls `stakingKeeper.GetRedelegations(ctx, delAddr, 1)` on the position's delegator address. If the redelegation completion time has not elapsed, this returns `true`, and the function immediately returns `ErrActiveRedelegation`. [4](#0-3) 

**Step 5 — Error propagates with no recovery:** There is no `continue`-on-error logic in `exitVestedAccountsPositions`. The error from `ForceFullExitWithDelegation` is wrapped and returned directly, causing `Migrate` and then `Migrate1to2` to return an error. [5](#0-4) 

**Step 6 — `isRedelegating` checks the position's delegator address, not the owner:** The comment on the check says "Defensive" — it was designed to guard the normal `ExitTierWithDelegation` user flow, not the migration path. The migration never clears or waits for active redelegations before calling `transferDelegationFromPosition`. [6](#0-5) 

### Impact Explanation

If any single vesting-owned position has an active redelegation at the time the v8 upgrade block is processed, `Migrate1to2` returns an error, the upgrade handler fails, and the chain halts. This is a chain-halting upgrade failure. Even a partial failure (one position out of many) aborts the entire migration because there is no per-position error recovery. Positions that were not yet exited retain their delegation and continue accruing rewards post-upgrade in an inconsistent state if the chain is somehow recovered via emergency governance.

### Likelihood Explanation

The standard Cosmos SDK unbonding/redelegation period is 21 days. An attacker (or any vesting account holder) who calls `TierRedelegate` within 21 days before the scheduled v8 upgrade block will have an active redelegation at migration time. `TierRedelegate` is a permissionless `MsgServer` endpoint callable by any position owner. The migration code in `v2/migrate.go` contains no logic to skip, defer, or handle positions with active redelegations. [7](#0-6) 

### Recommendation

In `exitVestedAccountsPositions` (or in `ForceFullExitWithDelegation`), before calling `transferDelegationFromPosition`, check `isRedelegating`. If the position is actively redelegating, either:

1. **Skip and log** the position (acceptable only if the redelegation will complete before the position can be abused post-upgrade), or
2. **Force-complete the redelegation** by advancing the staking module's redelegation queue for that delegator address before attempting the transfer, or
3. **Treat the position as already on the destination validator** (since `TierRedelegate` updates `pos.Delegation.ValidatorAddress` to the destination) and attempt the transfer from the destination validator directly.

The simplest safe fix is to skip positions with active redelegations during migration and schedule a follow-up cleanup once the redelegation period elapses, rather than failing the entire migration atomically.

### Proof of Concept

```go
// Keeper integration test sketch
func TestMigrate1to2_HaltsOnActiveRedelegation(t *testing.T) {
    // 1. Create a vesting account owner
    // 2. Create a tiered position owned by the vesting account
    // 3. Call TierRedelegate on the position (srcVal → dstVal)
    //    → creates redelegation on pos.DelegatorAddress with 21-day completion
    // 4. Do NOT advance block time past the redelegation completion time
    // 5. Call Migrate1to2
    // 6. Assert: error wraps ErrActiveRedelegation
    //    → migration halted
}
```

The redelegation entry is keyed to `pos.DelegatorAddress` (the module-derived account), confirmed at: [8](#0-7) 

The unconditional error return with no recovery path is at: [5](#0-4)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L245-255)
```go
	completionTime, unbondingID, err := ms.redelegate(ctx, delAddr, srcValAddr, dstValAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
	}
	// unbondingID 0 means the source validator is unbonded; redelegation is instant.
	// We skip mapping because no asynchronous completion hook will trigger.
	if unbondingID != 0 {
		if err := ms.setRedelegationMapping(ctx, unbondingID, pos.Id); err != nil {
			return nil, err
		}
	}
```

**File:** x/tieredrewards/keeper/delegation.go (L59-70)
```go
func (k Keeper) redelegate(ctx context.Context, delAddr sdk.AccAddress, srcValAddr, dstValAddr sdk.ValAddress, shares math.LegacyDec) (time.Time, uint64, error) {
	val, err := k.stakingKeeper.GetValidator(ctx, dstValAddr)
	if err != nil {
		return time.Time{}, 0, err
	}

	if !val.IsBonded() {
		return time.Time{}, 0, types.ErrValidatorNotBonded
	}

	return k.stakingKeeper.BeginRedelegation(ctx, delAddr, srcValAddr, dstValAddr, shares)
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

**File:** x/tieredrewards/keeper/transfer_delegation.go (L116-123)
```go
	// Defensive
	isRedelegating, err := k.isRedelegating(ctx, pos.DelegatorAddress)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}
	if isRedelegating {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, errorsmod.Wrapf(types.ErrActiveRedelegation, "position %d has an active redelegation", pos.Id)
	}
```
