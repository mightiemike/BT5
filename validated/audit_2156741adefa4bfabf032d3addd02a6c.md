### Title
Migration `backfillDelegatorAddress` Fails to Set Distribution Withdraw Routing for Legacy Delegator Addresses, Causing Base Rewards to Accumulate at Inaccessible Module-Derived Addresses — (`x/tieredrewards/migrations/v2/migrate.go`)

---

### Summary

`backfillDelegatorAddress` overwrites every position's `DelegatorAddress` with the legacy module-derived address but never calls `routeBaseRewardsToOwner` (i.e., `distributionKeeper.SetWithdrawAddr`) for those addresses. After the upgrade, every call to `claimBaseRewards` on a migrated position sends staking rewards to the legacy address itself rather than to the position owner, permanently locking those funds.

---

### Finding Description

**v1 state (pre-migration):**

The migration test confirms that v1 positions were stored with an empty `DelegatorAddress`:

```go
pos := types.NewPosition(id, testutil.TestOwner.String(), 1, "", 100, 0, now, true, now)
``` [1](#0-0) 

The actual staking delegation for each v1 position was held under the legacy address `authtypes.NewModuleAddress("tieredrewards/position/<id>")`. Because v1 predates the `routeBaseRewardsToOwner` feature, `SetWithdrawAddr` was **never called** for these legacy addresses.

**Migration (`backfillDelegatorAddress`):**

The migration iterates all positions and writes the legacy address into `DelegatorAddress`:

```go
pos.DelegatorAddress = LegacyDelegatorAddress(pos.Id)
// ...
if err := positions.Set(ctx, pos.Id, pos); err != nil { ... }
``` [2](#0-1) 

It does **not** call `routeBaseRewardsToOwner` (i.e., `distributionKeeper.SetWithdrawAddr(legacyAddr, ownerAddr)`) for any position. The distribution module's withdraw-address table for `legacyAddr` remains empty.

**Post-migration reward claiming:**

`claimBaseRewards` reads `pos.DelegatorAddress` and calls:

```go
rewards, err := k.distributionKeeper.WithdrawDelegationRewards(ctx, posDelAddr, valAddr)
``` [3](#0-2) 

The comment on the function even states the invariant that is now broken:

```go
// This assumes that the delegation withdrawAddress has been set to the position's owner address.
``` [4](#0-3) 

Because no withdraw address was ever set for `legacyAddr`, the Cosmos SDK distribution module defaults to sending rewards to `legacyAddr` itself. `legacyAddr` is `authtypes.NewModuleAddress("tieredrewards/position/<id>")` — a deterministic module-derived address that no user controls and that is not registered as a standard module account with a minter/burner permission. Funds sent there are permanently inaccessible.

**Contrast with v2 position creation:**

New positions created after the upgrade correctly call `routeBaseRewardsToOwner` during `createDelegatedPosition`:

```go
if err := k.routeBaseRewardsToOwner(ctx, delAddr, ownerAddr); err != nil {
    return types.Position{}, err
}
``` [5](#0-4) 

where `routeBaseRewardsToOwner` is:

```go
func (k Keeper) routeBaseRewardsToOwner(ctx context.Context, posDelAddr, ownerAddr sdk.AccAddress) error {
    return k.distributionKeeper.SetWithdrawAddr(ctx, posDelAddr, ownerAddr)
}
``` [6](#0-5) 

The migration omits this exact step for every migrated position.

---

### Impact Explanation

Every v1 position holder loses **all base staking rewards** accrued after the v8 upgrade. The rewards are not burned — they are sent to the legacy module-derived address — but no user can sign transactions from that address. The loss is proportional to the staked amount and the time the position remains open post-upgrade. This affects every call path that invokes `claimBaseRewards`: `ClaimTierRewards`, `TierUndelegate`, `TierRedelegate`, `AddToTierPosition`, `ClearPosition`, `ExitTierWithDelegation`, and the ABCI-triggered `claimRewardsAndUpdateTierPositions`. [7](#0-6) 

---

### Likelihood Explanation

The vulnerability is triggered automatically for every v1 position the moment any reward-claiming code path executes post-upgrade. No attacker action is required; it is a systemic failure affecting all migrated positions. The upgrade path is a supported production path explicitly listed in the scope. [8](#0-7) 

---

### Recommendation

Add a `routeBaseRewardsToOwner` call inside `backfillDelegatorAddress` for each position immediately after setting `DelegatorAddress`:

```go
ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
if err != nil { return err }
legacyAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
if err != nil { return err }
if err := distrKeeper.SetWithdrawAddr(ctx, legacyAddr, ownerAddr); err != nil {
    return fmt.Errorf("set withdraw addr for position %d: %w", pos.Id, err)
}
```

The `Migrate` function signature must be extended to accept a `DistributionKeeper` (or a narrow interface exposing `SetWithdrawAddr`), mirroring how it already accepts `AccountKeeper` and `PositionForceExiter`. [9](#0-8) 

---

### Proof of Concept

1. Create a v1 position by directly writing a `types.Position` with `DelegatorAddress = ""` and a live delegation under `LegacyDelegatorAddress(id)` to a bonded validator.
2. Run `Migrate1to2`. Verify `pos.DelegatorAddress` is now `LegacyDelegatorAddress(id)`.
3. Verify `distrKeeper.GetDelegatorWithdrawAddr(legacyAddr)` returns `legacyAddr` itself (no routing set).
4. Advance a block and allocate staking rewards to the validator.
5. Call `ClaimTierRewards` for the position.
6. Assert the owner's balance is **unchanged** and `legacyAddr`'s balance increased by the reward amount — confirming rewards were misdirected.

### Citations

**File:** x/tieredrewards/keeper/migrations_test.go (L26-26)
```go
		pos := types.NewPosition(id, testutil.TestOwner.String(), 1, "", 100, 0, now, true, now)
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L28-41)
```go
func Migrate(
	ctx context.Context,
	positions collections.Map[uint64, types.Position],
	ak AccountKeeper,
	pk PositionForceExiter,
) error {
	if err := backfillDelegatorAddress(ctx, positions); err != nil {
		return fmt.Errorf("backfill delegator address: %w", err)
	}
	if err := exitVestedAccountsPositions(ctx, positions, ak, pk); err != nil {
		return fmt.Errorf("exit vested accounts positions: %w", err)
	}
	return nil
}
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L57-65)
```go
	for _, kv := range kvs {
		pos := kv.Value
		pos.DelegatorAddress = LegacyDelegatorAddress(pos.Id)
		if _, err := sdk.AccAddressFromBech32(pos.DelegatorAddress); err != nil {
			return fmt.Errorf("backfill produced invalid delegator address for position %d: %w", pos.Id, err)
		}
		if err := positions.Set(ctx, pos.Id, pos); err != nil {
			return err
		}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L15-17)
```go
// claimBaseRewards claims the outstanding base rewards held
// by the given position's delegation for a single validator.
// This assumes that the delegation withdrawAddress has been set to the position's owner address.
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L18-46)
```go
func (k Keeper) claimBaseRewards(ctx context.Context, pos types.PositionState) (sdk.Coins, error) {
	if !pos.IsDelegated() {
		return sdk.NewCoins(), nil
	}
	valAddr, err := sdk.ValAddressFromBech32(pos.Delegation.ValidatorAddress)
	if err != nil {
		return nil, err
	}
	posDelAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}
	rewards, err := k.distributionKeeper.WithdrawDelegationRewards(ctx, posDelAddr, valAddr)
	if err != nil {
		return nil, err
	}
	if rewards.IsZero() {
		return rewards, nil
	}
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventBaseRewardsClaimed{
		PositionId: pos.Id,
		Owner:      pos.Owner,
		Rewards:    rewards,
	}); err != nil {
		return nil, err
	}
	return rewards, nil
}
```

**File:** x/tieredrewards/keeper/position.go (L63-65)
```go
	if err := k.routeBaseRewardsToOwner(ctx, delAddr, ownerAddr); err != nil {
		return types.Position{}, err
	}
```

**File:** x/tieredrewards/keeper/position.go (L106-108)
```go
func (k Keeper) routeBaseRewardsToOwner(ctx context.Context, posDelAddr, ownerAddr sdk.AccAddress) error {
	return k.distributionKeeper.SetWithdrawAddr(ctx, posDelAddr, ownerAddr)
}
```

**File:** x/tieredrewards/keeper/migrations.go (L17-19)
```go
func (m Migrator) Migrate1to2(ctx sdk.Context) error {
	return v2.Migrate(ctx, m.keeper.Positions, m.keeper.accountKeeper, m.keeper)
}
```
