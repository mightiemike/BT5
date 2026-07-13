### Title
Static Module-Account List in `x/supply` Never Updated After New Modules Are Added — (`x/supply/`)

### Summary

The `x/supply` module computes `liquid_supply` by subtracting a **hardcoded, static list of module-account balances** from total supply. This list was fixed at the time the module was written. When new modules with their own module accounts are added to the chain (e.g., `x/tieredrewards` with `types.ModuleName` and `types.RewardsPoolName`), those accounts are never added to the list. The result is that tokens locked in those module accounts are permanently counted as liquid supply, overstating the circulating supply by the full balance of every unlisted module account.

---

### Finding Description

`x/supply` computes liquid supply as:

```
liquid_supply = total_supply − (unvested_supply + module_account_balance)
```

The `module_account_balance` term is derived from a **static list** hardcoded in the module:

```go
// ModuleAccounts defines the module accounts which will be queried to get liquid supply
ModuleAccounts = []string{
    authtypes.FeeCollectorName,
    distrtypes.ModuleName,
    stakingtypes.BondedPoolName,
    stakingtypes.NotBondedPoolName,
    minttypes.ModuleName,
    govtypes.ModuleName,
}
``` [1](#0-0) 

This list is initialized once and never updated. The `x/tieredrewards` module, added later, materialises two module accounts at `InitGenesis`:

```go
tierModuleAddr   := s.app.AccountKeeper.GetModuleAddress(types.ModuleName)
rewardsPoolAddr  := s.app.AccountKeeper.GetModuleAddress(types.RewardsPoolName)
``` [2](#0-1) 

Neither `types.ModuleName` nor `types.RewardsPoolName` appears in `x/supply`'s `ModuleAccounts` list. Any CRO held in the tieredrewards rewards pool (funded by governance to pay bonus rewards) is therefore **not subtracted** from total supply when computing `liquid_supply`. The same applies to any other module account added after the static list was written (e.g., `x/nft-transfer` escrow accounts, `x/inflation` accounts).

The desynchronization is structurally identical to the reported BathPair/BathHouse issue: one module (`x/supply`) reads a value set from the broader chain state at a single point in time and never re-syncs, while the authoritative source (the set of live module accounts) continues to evolve.

---

### Impact Explanation

The `LiquidSupply` query returns an inflated value equal to `Σ balance(unlisted_module_account)` for every module account not in the static list. For the tieredrewards rewards pool alone, this can be a large CRO balance (the pool is funded by governance to sustain bonus APY across all tier positions). Any explorer, wallet, or application consuming this query presents incorrect circulating-supply data to users and investors.

The corrupted value is: **`liquid_supply` (supply figure)** — overstated by the sum of balances held in module accounts absent from the static list.

---

### Likelihood Explanation

This is already occurring on mainnet. The `x/tieredrewards` module was added after `x/supply`'s static list was written, and its module accounts are confirmed to be materialised at `InitGenesis`. No attacker action is required; the desynchronization is a structural consequence of the static list never being updated. Any call to the `LiquidSupply` gRPC/REST endpoint returns the incorrect value today. [3](#0-2) 

---

### Recommendation

Either (a) replace the static `ModuleAccounts` list with a dynamic lookup that iterates all registered module accounts from `x/auth` at query time, or (b) proceed with the already-proposed deprecation of `x/supply` (ADR-005) and direct consumers to the Cosmos SDK's native liquid-supply solution. Until one of these is done, the `LiquidSupply` endpoint should be documented as returning a lower bound, not the true circulating supply. [4](#0-3) 

---

### Proof of Concept

1. Fund the tieredrewards rewards pool with, say, 10,000,000 CRO via governance.
2. Query `GET /cosmos/supply/v1beta1/liquid_supply` (or the equivalent gRPC endpoint).
3. Observe that the returned `liquid_supply` includes the 10,000,000 CRO sitting in the rewards pool module account, because `types.RewardsPoolName` is absent from `ModuleAccounts`.
4. Query `x/bank` for the balance of the rewards-pool module address directly; confirm the balance is non-zero and equals the discrepancy between the reported liquid supply and the true circulating supply.

The static list is the sole root cause; no privileged action or key compromise is required to trigger the incorrect result. [5](#0-4) [6](#0-5)

### Citations

**File:** doc/architecture/adr-005.md (L8-38)
```markdown
The current `x/supply` module tracks liquid supply of a given token using the following formula:

```
liquid_supply = total_supply - (unvested_supply + module_account_balance)
```

where,

- `total_supply`: Total supply of a `denom` which is obtained from `x/bank` module.
- `unvested_supply`: The sum of tokens locked in vesting accounts (`x/supply` maintains a static list of vesting
  accounts configured in `genesis.json`, it does not support adding/removing vesting accounts).
- `module_account_balance`: The sum of tokens locked in module accounts of different modules (`x/supply` maintains a
  static list of module accounts that it uses to fetch total tokens locked in module accounts)

Current module account list:

```
// ModuleAccounts defines the module accounts which will be queried to get liquid supply
ModuleAccounts = []string{
	authtypes.FeeCollectorName,
	distrtypes.ModuleName,
	stakingtypes.BondedPoolName,
	stakingtypes.NotBondedPoolName,
	minttypes.ModuleName,
	govtypes.ModuleName,
}
```

To accurately calculate `liquid_supply`, `x/supply` module needs updated list of all the vesting accounts and module
accounts. Also, for all the vesting accounts and module accounts, it loops over them and fetches their balance
one-by-one (which'll not be efficient if there are a lot of vesting accounts).
```

**File:** doc/architecture/adr-005.md (L43-46)
```markdown
https://github.com/cosmos/cosmos-sdk/issues/7774 for getting liquid supply of a token. 

For the short-term, ability to calculate liquid supply will be added in explorer: https://github.com/crypto-com/chain-indexing/issues/700.

```

**File:** x/tieredrewards/keeper/genesis_test.go (L265-276)
```go
func (s *KeeperSuite) TestInitGenesis_MaterializesModuleAccounts() {
	tierModuleAddr := s.app.AccountKeeper.GetModuleAddress(types.ModuleName)
	rewardsPoolAddr := s.app.AccountKeeper.GetModuleAddress(types.RewardsPoolName)

	s.keeper.InitGenesis(s.ctx, types.DefaultGenesisState())

	for _, addr := range []sdk.AccAddress{tierModuleAddr, rewardsPoolAddr} {
		acc := s.app.AccountKeeper.GetAccount(s.ctx, addr)
		s.Require().NotNil(acc, "module account should exist after InitGenesis")
		_, ok := acc.(sdk.ModuleAccountI)
		s.Require().True(ok, "account at %s should be a module account", addr.String())
	}
```
