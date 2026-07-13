### Title
`GetUnvestedSupply` Uses Stale Genesis Snapshot — Post-Genesis Vesting Accounts' Locked Coins Not Subtracted from Liquid Supply — (`x/supply/keeper/keeper.go`, `x/supply/keeper/genesis.go`)

---

### Summary

`GetLiquidSupply` permanently undercounts the unvested (locked) supply for any vesting account created after genesis, because the vesting-account list it consults is a one-time KV snapshot written only during `InitGenesis` and never updated.

---

### Finding Description

`InitGenesis` calls `FetchVestingAccounts` — which live-iterates all accounts — exactly once and persists the result via `SetVestingAccounts`: [1](#0-0) 

`SetVestingAccounts` is called in **no other location** in the entire codebase: [2](#0-1) 

`GetUnvestedSupply` reads exclusively from that static KV snapshot via `GetVestingAccounts`, not from a live account iteration: [3](#0-2) 

`GetLiquidSupply` subtracts only what `GetUnvestedSupply` returns: [4](#0-3) 

Any vesting account created post-genesis (e.g., via the standard Cosmos SDK `MsgCreateVestingAccount` from `x/auth/vesting`) is never added to the stored list. Its `LockedCoins` are therefore never subtracted, and `GetLiquidSupply` returns a value inflated by exactly those locked coins.

`FetchVestingAccounts` — the function that *would* capture the new account — is never called again after genesis: [5](#0-4) 

---

### Impact Explanation

The `LiquidSupply` gRPC query returns an inflated figure. Any consumer of this endpoint (block explorers, exchanges, governance tooling, or on-chain modules that read liquid supply) will see a liquid supply that includes coins that are actually locked in vesting accounts. The invariant "all locked vesting coins are excluded from liquid supply" is permanently broken for every post-genesis vesting account for the lifetime of the chain.

---

### Likelihood Explanation

`MsgCreateVestingAccount` is a standard, permissionless Cosmos SDK message available to any user. No governance or privileged role is required. The broken state is triggered by the first post-genesis vesting account creation and persists indefinitely because there is no repair path.

---

### Recommendation

`SetVestingAccounts` must be updated whenever a vesting account is created or converted. The cleanest fix is to replace the static snapshot approach entirely: have `GetUnvestedSupply` call `FetchVestingAccounts` directly (live iteration) instead of reading from the KV store, or register an `AccountKeeper` hook that calls `SetVestingAccounts` on every new vesting account creation.

---

### Proof of Concept

1. `InitGenesis` runs; `SetVestingAccounts` stores the genesis-time vesting account list.
2. A user broadcasts `MsgCreateVestingAccount` creating account `V` with 1 000 000 uatom locked.
3. `GetVestingAccounts` returns the old list — `V` is absent.
4. `GetUnvestedSupply` iterates the old list, never calls `bankKeeper.LockedCoins` for `V`.
5. `GetLiquidSupply` = `TotalSupply` − `ModuleBalances` − `(unvested without V's coins)`.
6. Result is 1 000 000 uatom higher than the true liquid supply.

A keeper unit test can confirm this by calling `InitGenesis`, creating a new vesting account, then asserting `GetLiquidSupply` incorrectly includes the new account's locked coins.

### Citations

**File:** x/supply/keeper/genesis.go (L10-12)
```go
func (k Keeper) InitGenesis(ctx sdk.Context, genState types.GenesisState) {
	k.SetVestingAccounts(ctx, k.FetchVestingAccounts(ctx))
}
```

**File:** x/supply/keeper/keeper.go (L54-68)
```go
func (k Keeper) FetchVestingAccounts(ctx sdk.Context) types.VestingAccounts {
	var addresses []string

	k.accountKeeper.IterateAccounts(ctx, func(account sdk.AccountI) bool {
		vacc, ok := account.(vestexported.VestingAccount)
		if ok {
			addresses = append(addresses, vacc.GetAddress().String())
		}
		return false
	})

	return types.VestingAccounts{
		Addresses: addresses,
	}
}
```

**File:** x/supply/keeper/keeper.go (L71-75)
```go
func (k Keeper) SetVestingAccounts(ctx sdk.Context, vestingAccounts types.VestingAccounts) {
	store := ctx.KVStore(k.storeKey)
	b := k.cdc.MustMarshal(&vestingAccounts)
	store.Set(types.VestingAccountsKey, b)
}
```

**File:** x/supply/keeper/keeper.go (L104-119)
```go
func (k Keeper) GetUnvestedSupply(ctx sdk.Context) sdk.Coins {
	vestingAccounts := k.GetVestingAccounts(ctx)

	var lockedCoins sdk.Coins

	for _, vestingAccountAddress := range vestingAccounts.GetAddresses() {
		addr, err := sdk.AccAddressFromBech32(vestingAccountAddress)
		if err != nil {
			panic(err)
		}

		lockedCoins = lockedCoins.Add(k.bankKeeper.LockedCoins(ctx, addr)...)
	}

	return lockedCoins
}
```

**File:** x/supply/keeper/keeper.go (L144-149)
```go
func (k Keeper) GetLiquidSupply(ctx sdk.Context) sdk.Coins {
	totalSupply := k.GetTotalSupply(ctx)
	unvestedSupply := k.GetUnvestedSupply(ctx)
	moduleAccountBalance := k.GetTotalModuleAccountBalance(ctx, ModuleAccounts...)

	return totalSupply.Sub(unvestedSupply...).Sub(moduleAccountBalance...)
```
