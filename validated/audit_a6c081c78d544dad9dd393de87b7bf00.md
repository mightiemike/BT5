### Title
Missing Storage Gaps in Intermediate Upgradeable Inheritance Contracts — (`EndpointGated`, `ClearinghouseStorage`, `BaseEngine`, `SpotEngineState`, `PerpEngineState`, `EndpointStorage`)

---

### Summary

Every upgradeable proxy contract in Nado (`Clearinghouse`, `SpotEngine`, `PerpEngine`, `Endpoint`) inherits from intermediate abstract contracts that declare state variables but define **no `__gap` storage reserve**. A future upgrade that adds even a single new variable to any of these intermediate contracts will silently shift the storage layout of all child contracts, corrupting every critical protocol variable — user balances, insurance funds, product mappings, and engine references.

---

### Finding Description

The Nado protocol uses OpenZeppelin Transparent Upgradeable Proxies for its core contracts. The upgrade-safe pattern requires that every non-leaf contract in an upgradeable inheritance chain reserves storage slots via a `uint256[N] private __gap` array. Without gaps, inserting a new variable into a parent contract shifts all child-contract storage slots by the number of new slots added.

A search across the entire `core/contracts/` directory confirms **zero occurrences** of `__gap` in any production Solidity file.

The affected inheritance chains are:

**`Clearinghouse` (proxy)**
```
Clearinghouse (proxy)
  ├── EndpointGated          ← address private endpoint  [NO GAP]
  │     └── OwnableUpgradeable                           [has gap ✓]
  └── ClearinghouseStorage   ← quote, clearinghouse,     [NO GAP]
                                clearinghouseLiq,
                                productToEngine,
                                engineByType,
                                supportedEngines,
                                insurance,
                                lastLiquidationFees,
                                spreads, withdrawPool
```

**`SpotEngine` / `PerpEngine` (proxies)**
```
SpotEngine / PerpEngine (proxy)
  └── SpotEngineState / PerpEngineState  ← configs, states, balances  [NO GAP]
        └── BaseEngine                   ← _clearinghouse, productIds, [NO GAP]
                                            canApplyDeltas,
                                            nonZeroBalances
              └── EndpointGated          ← address private endpoint    [NO GAP]
                    └── OwnableUpgradeable                             [has gap ✓]
```

**`Endpoint` (proxy)**
```
Endpoint (proxy)
  ├── EIP712Upgradeable                                  [has gap ✓]
  ├── OwnableUpgradeable                                 [has gap ✓]
  └── EndpointStorage        ← clearinghouse, spotEngine,[NO GAP]
                                perpEngine, sanctions,
                                sequencer, sequencerFees,
                                subaccountIds, subaccounts,
                                numSubaccounts, nonces,
                                nSubmissions, slowModeConfig,
                                slowModeTxs, times,
                                sequencerFee, linkedSigners,
                                nlpSigners, nlpPools,
                                slowModeFees, referralCodes,
                                priceX18, offchainExchange,
                                verifier, endpointTx
```

The most critical instance is `EndpointGated`, which is the shared base for `Clearinghouse`, `SpotEngine`, and `PerpEngine`. It declares one storage variable: [1](#0-0) 

Adding any new variable to `EndpointGated` in a future upgrade shifts every slot in `ClearinghouseStorage` and `BaseEngine` by one position.

`ClearinghouseStorage` itself also has no gap: [2](#0-1) 

`BaseEngine` has no gap: [3](#0-2) 

`EndpointStorage` has no gap: [4](#0-3) 

`SpotEngineState` has no gap: [5](#0-4) 

`PerpEngineState` has no gap: [6](#0-5) 

All of these contracts are in the inheritance chain of proxies managed by `ProxyManager.migrateAll()`: [7](#0-6) 

---

### Impact Explanation

If any future upgrade adds a new variable to `EndpointGated`, `ClearinghouseStorage`, `BaseEngine`, `SpotEngineState`, `PerpEngineState`, or `EndpointStorage`, the storage layout of the child proxy contracts shifts. Concretely:

- **`Clearinghouse`**: `quote` (the collateral token address), `clearinghouseLiq` (the liquidation delegate), `insurance` (the insurance fund balance), `productToEngine` (product routing), and `withdrawPool` would all read from wrong slots — causing silent misrouting of funds, broken liquidations, and corrupted insurance accounting.
- **`SpotEngine`**: `configs`, `states`, and `balances` (all user spot balances and interest-rate state) would be read from wrong slots, corrupting every user's collateral balance.
- **`PerpEngine`**: `states` and `balances` (all perp positions and funding state) would be corrupted, breaking PnL settlement and health calculations.
- **`Endpoint`**: `clearinghouse`, `spotEngine`, `perpEngine`, `sequencer`, `linkedSigners`, `nonces`, `slowModeTxs`, and `endpointTx` would all be corrupted, breaking the entire transaction pipeline.

---

### Likelihood Explanation

The protocol has an active upgrade system (`ProxyManager`, `migrateAll`, `bootstrapEndpointTx`) and is under active development. The Midas protocol suffered this exact class of bug twice — once in the original audit and again after new contracts were introduced. Given that Nado's intermediate contracts (`EndpointGated`, `BaseEngine`, etc.) are shared across multiple proxies and are likely candidates for future feature additions (e.g., new access control flags, new configuration variables), the probability of a storage-corrupting upgrade is high without explicit gap protection.

---

### Recommendation

Add `uint256[N] private __gap` arrays to every non-leaf abstract contract in the upgradeable inheritance chains. The gap size should be chosen so that the total storage used by the contract plus the gap equals a round number (e.g., 50 slots):

```solidity
// EndpointGated.sol
abstract contract EndpointGated is OwnableUpgradeable, IEndpointGated {
    address private endpoint;
    uint256[49] private __gap; // 1 slot used, 49 reserved = 50 total
}

// ClearinghouseStorage.sol
abstract contract ClearinghouseStorage {
    address internal quote;
    address internal clearinghouse;
    address internal clearinghouseLiq;
    mapping(uint32 => IProductEngine) internal productToEngine;
    mapping(IProductEngine.EngineType => IProductEngine) internal engineByType;
    IProductEngine.EngineType[] internal supportedEngines;
    int128 internal insurance;
    int128 internal lastLiquidationFees;
    uint256 internal spreads;
    address internal withdrawPool;
    uint256[40] private __gap; // adjust N based on actual slot count
}

// BaseEngine.sol, SpotEngineState.sol, PerpEngineState.sol, EndpointStorage.sol
// — add equivalent __gap arrays
```

Use OpenZeppelin's `@openzeppelin/upgrades-core` storage layout checker or `hardhat-upgrades` plugin to validate storage compatibility before every upgrade.

---

### Proof of Concept

1. Deploy `Clearinghouse` proxy with current implementation. Note `clearinghouseLiq` is stored at slot `S`.
2. Prepare a new `EndpointGated` implementation that adds one new `address` variable before `endpoint`.
3. Upgrade `Clearinghouse` via `ProxyManager.migrateAll()`.
4. Read `clearinghouseLiq` from the proxy — it now returns the value previously stored at slot `S-1` (the old `endpoint` value), not the actual liquidation contract address.
5. Any subsequent call to `liquidateSubaccount` will `delegatecall` to the wrong address, silently failing or executing arbitrary logic.

### Citations

**File:** core/contracts/EndpointGated.sol (L10-11)
```text
abstract contract EndpointGated is OwnableUpgradeable, IEndpointGated {
    address private endpoint;
```

**File:** core/contracts/ClearinghouseStorage.sol (L8-29)
```text
abstract contract ClearinghouseStorage {
    using MathSD21x18 for int128;

    // Each clearinghouse has a quote ERC20
    address internal quote;
    address internal clearinghouse;
    address internal clearinghouseLiq;

    // product ID -> engine address
    mapping(uint32 => IProductEngine) internal productToEngine;
    // Type to engine address
    mapping(IProductEngine.EngineType => IProductEngine) internal engineByType;
    // Supported engine types
    IProductEngine.EngineType[] internal supportedEngines;

    int128 internal insurance;

    int128 internal lastLiquidationFees;

    uint256 internal spreads;

    address internal withdrawPool;
```

**File:** core/contracts/BaseEngine.sol (L15-27)
```text
abstract contract BaseEngine is IProductEngine, EndpointGated {
    using MathSD21x18 for int128;

    IClearinghouse internal _clearinghouse;
    uint32[] internal productIds;

    mapping(address => bool) internal canApplyDeltas;

    // subaccount -> bitmapIndex -> bitmapChunk
    mapping(bytes32 => mapping(uint32 => uint256)) internal nonZeroBalances;

    bytes32 internal constant RISK_STORAGE = keccak256("nado.protocol.risk");

```

**File:** core/contracts/EndpointStorage.sol (L19-66)
```text
abstract contract EndpointStorage {
    using ERC20Helper for IERC20Base;

    IClearinghouse public clearinghouse;
    ISpotEngine internal spotEngine;
    IPerpEngine internal perpEngine;
    ISanctionsList internal sanctions;

    address internal sequencer;
    int128 internal sequencerFees;

    mapping(bytes32 => uint64) internal subaccountIds;
    mapping(uint64 => bytes32) internal subaccounts;
    uint64 internal numSubaccounts;

    mapping(address => uint64) internal nonces;

    uint64 public nSubmissions;

    IEndpoint.SlowModeConfig internal slowModeConfig;
    mapping(uint64 => IEndpoint.SlowModeTx) internal slowModeTxs;

    struct Times {
        uint128 perpTime;
        uint128 spotTime;
    }

    Times internal times;

    mapping(uint32 => int128) internal sequencerFee;

    mapping(bytes32 => address) internal linkedSigners;

    mapping(bytes32 => address) internal nlpSigners;
    IEndpoint.NlpPool[] public nlpPools;

    int128 internal slowModeFees;

    // invitee -> referralCode
    mapping(address => string) public referralCodes; // deprecated

    mapping(uint32 => int128) internal priceX18;
    address internal offchainExchange;

    IVerifier internal verifier;

    address internal endpointTx;

```

**File:** core/contracts/SpotEngineState.sol (L7-13)
```text
abstract contract SpotEngineState is ISpotEngine, BaseEngine {
    using MathSD21x18 for int128;

    mapping(uint32 => Config) internal configs;
    mapping(uint32 => State) internal states;
    mapping(uint32 => mapping(bytes32 => BalanceNormalized)) internal balances;
    mapping(bytes32 => NlpLockedBalanceQueue) internal nlpLockedBalanceQueues;
```

**File:** core/contracts/PerpEngineState.sol (L13-22)
```text
abstract contract PerpEngineState is IPerpEngine, BaseEngine {
    using MathSD21x18 for int128;

    mapping(uint32 => State) public states;
    mapping(uint32 => mapping(bytes32 => Balance)) public balances;

    // we use this to track if we have migrated the state to the new format
    // currently we have migrationFlag = 1
    uint128 public migrationFlag;

```

**File:** core/contracts/BaseProxyManager.sol (L189-201)
```text
    function migrateAll(NewImpl[] calldata newImpls) external onlyOwner {
        for (uint32 i = 0; i < newImpls.length; i++) {
            if (_isEqual(newImpls[i].name, CLEARINGHOUSE_LIQ)) {
                _migrateClearinghouseLiq(newImpls[i]);
            } else if (_isEqual(newImpls[i].name, ENDPOINT_TX)) {
                _migrateEndpointTx(newImpls[i]);
            } else {
                _migrateRegularProxy(newImpls[i]);
            }
            codeHashes[newImpls[i].name] = pendingHashes[newImpls[i].name];
        }
        require(!hasPending(), "still having pending impls to be migrated.");
    }
```
