### Title
Missing Storage Gaps in Abstract Upgradeable Base Contracts Across Engine and Clearinghouse Hierarchy — (`EndpointGated.sol`, `BaseEngine.sol`, `ClearinghouseStorage.sol`, `BaseWithdrawPool.sol`, `BaseProxyManager.sol`)

---

### Summary

Multiple abstract contracts that form the inheritance base of Nado's core upgradeable contracts (`SpotEngine`, `PerpEngine`, `Clearinghouse`, `WithdrawPool`, `ProxyManager`) declare state variables but contain no `__gap` storage reservation. If any of these abstract contracts are extended with new state variables in a future upgrade, the storage layout of all child contracts will shift, silently corrupting critical protocol state including subaccount balances, product configurations, risk parameters, and linked signers.

---

### Finding Description

The Nado protocol uses the OpenZeppelin upgradeable proxy pattern across its core contracts. The following abstract contracts declare state variables but omit the `uint256[N] private __gap` reservation required to safely extend storage in future upgrades:

**1. `EndpointGated` — deepest base, widest blast radius**

`EndpointGated` is `abstract` and inherits `OwnableUpgradeable`. It declares one state variable:

```solidity
abstract contract EndpointGated is OwnableUpgradeable, IEndpointGated {
    address private endpoint;   // occupies 1 storage slot, no __gap
``` [1](#0-0) 

`EndpointGated` is the root base for the entire engine and clearinghouse hierarchy:
- `Clearinghouse` inherits `EndpointGated` directly [2](#0-1) 
- `BaseEngine` inherits `EndpointGated`, and `SpotEngine` / `PerpEngine` inherit through `SpotEngineState` / `PerpEngineState` → `BaseEngine` [3](#0-2) 

**2. `BaseEngine` — engine state base, no gap**

`BaseEngine` declares four state variables with no `__gap`:

```solidity
abstract contract BaseEngine is IProductEngine, EndpointGated {
    IClearinghouse internal _clearinghouse;
    uint32[] internal productIds;
    mapping(address => bool) internal canApplyDeltas;
    mapping(bytes32 => mapping(uint32 => uint256)) internal nonZeroBalances;
``` [4](#0-3) 

**3. `ClearinghouseStorage` — clearinghouse state base, no gap**

`ClearinghouseStorage` declares 9 state variables (including `insurance`, `spreads`, `withdrawPool`, engine mappings) with no `__gap`: [5](#0-4) 

**4. `BaseWithdrawPool` — withdraw pool base, no gap**

`BaseWithdrawPool` inherits `EIP712Upgradeable` and `OwnableUpgradeable`, declares `clearinghouse`, `verifier`, `markedIdxs`, `fees`, `minIdx` with no `__gap`: [6](#0-5) 

**5. `BaseProxyManager` — proxy manager base, no gap**

`BaseProxyManager` inherits `OwnableUpgradeable` and declares `submitter`, `proxyManagerHelper`, `contractNames`, `proxies`, `pendingImpls`, `pendingHashes`, `codeHashes` with no `__gap`: [7](#0-6) 

No `__gap` variable exists anywhere in the codebase — confirmed by a full-repo search for `__gap` returning zero matches.

---

### Impact Explanation

The concrete storage collision scenario for the highest-impact case (`EndpointGated` → `BaseEngine` → `SpotEngine`/`PerpEngine`):

| Slot | Current layout | Layout after adding 1 var to `EndpointGated` |
|------|---------------|----------------------------------------------|
| 51 | `EndpointGated.endpoint` | `EndpointGated.endpoint` |
| 52 | `BaseEngine._clearinghouse` | `EndpointGated.newVar` ← **NEW** |
| 53 | `BaseEngine.productIds` | `BaseEngine._clearinghouse` ← **SHIFTED** |
| 54 | `BaseEngine.canApplyDeltas` | `BaseEngine.productIds` ← **SHIFTED** |
| 55 | `BaseEngine.nonZeroBalances` | `BaseEngine.canApplyDeltas` ← **SHIFTED** |
| 56 | `SpotEngineState.configs` | `BaseEngine.nonZeroBalances` ← **SHIFTED** |
| 57 | `SpotEngineState.states` | `SpotEngineState.configs` ← **SHIFTED** |
| 58 | `SpotEngineState.balances` | `SpotEngineState.states` ← **SHIFTED** |

A single new variable in `EndpointGated` would corrupt `_clearinghouse`, `productIds`, `canApplyDeltas`, `nonZeroBalances`, all spot/perp `states`, all `balances`, and `nlpLockedBalanceQueues` in both `SpotEngine` and `PerpEngine`. This would result in:
- Subaccount balances reading from wrong storage slots → incorrect health calculations → improper liquidations or blocked withdrawals
- `canApplyDeltas` mapping corrupted → unauthorized addresses could apply balance deltas, or authorized ones could be blocked
- `_clearinghouse` address corrupted → all engine→clearinghouse calls would fail or route to wrong address

For `ClearinghouseStorage`, a new variable would corrupt `insurance`, `spreads`, `withdrawPool`, and engine routing mappings in `Clearinghouse`.

---

### Likelihood Explanation

The likelihood is **Low** in isolation — it requires the protocol team to add a new state variable to one of these abstract contracts during an upgrade. However, the protocol already demonstrates active upgrade patterns (`reinitializer(2)` in `BaseProxyManager.bootstrapEndpointTx`, `upgradeClearinghouseLiq`, `upgradeEndpointTx`), confirming that upgrades are a live operational activity. [8](#0-7) 

The absence of gaps across the entire codebase means any future extension of any of these five abstract contracts will silently corrupt live protocol state with no on-chain warning.

---

### Recommendation

Add `uint256[N] private __gap` at the end of each abstract contract's storage declarations, where `N` is chosen so that the total slot count for the contract reaches a round number (commonly 50):

```solidity
// EndpointGated.sol
abstract contract EndpointGated is OwnableUpgradeable, IEndpointGated {
    address private endpoint;
    uint256[49] private __gap; // reserves 49 slots (1 used by endpoint)
}

// BaseEngine.sol
abstract contract BaseEngine is IProductEngine, EndpointGated {
    IClearinghouse internal _clearinghouse;
    uint32[] internal productIds;
    mapping(address => bool) internal canApplyDeltas;
    mapping(bytes32 => mapping(uint32 => uint256)) internal nonZeroBalances;
    uint256[46] private __gap; // reserves 46 slots (4 used above)
}
```

Apply the same pattern to `ClearinghouseStorage`, `BaseWithdrawPool`, and `BaseProxyManager`.

---

### Proof of Concept

1. Deploy `SpotEngine` behind a `TransparentUpgradeableProxy`.
2. Record the storage slot of `_clearinghouse` (slot 52 in the current layout).
3. Upgrade `EndpointGated` to a version that adds `address private newVar` after `endpoint`.
4. Upgrade `SpotEngine` to the new implementation.
5. Read slot 52 — it now contains `newVar` (zero/uninitialized), while `_clearinghouse` has shifted to slot 53.
6. Call `SpotEngine.getClearinghouse()` — it returns `address(0)` instead of the real clearinghouse address, breaking all engine→clearinghouse interactions.
7. Any call that routes through `_clearinghouse` (e.g., `registerProduct`, `getSlowModeFee`) will revert or silently operate against address zero.

### Citations

**File:** core/contracts/EndpointGated.sol (L10-11)
```text
abstract contract EndpointGated is OwnableUpgradeable, IEndpointGated {
    address private endpoint;
```

**File:** core/contracts/Clearinghouse.sol (L21-21)
```text
contract Clearinghouse is EndpointGated, ClearinghouseStorage, IClearinghouse {
```

**File:** core/contracts/BaseEngine.sol (L15-24)
```text
abstract contract BaseEngine is IProductEngine, EndpointGated {
    using MathSD21x18 for int128;

    IClearinghouse internal _clearinghouse;
    uint32[] internal productIds;

    mapping(address => bool) internal canApplyDeltas;

    // subaccount -> bitmapIndex -> bitmapChunk
    mapping(bytes32 => mapping(uint32 => uint256)) internal nonZeroBalances;
```

**File:** core/contracts/ClearinghouseStorage.sol (L8-30)
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

**File:** core/contracts/BaseWithdrawPool.sol (L14-43)
```text
abstract contract BaseWithdrawPool is EIP712Upgradeable, OwnableUpgradeable {
    using ERC20Helper for IERC20Base;
    using MathSD21x18 for int128;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function _initialize(address _clearinghouse, address _verifier)
        internal
        initializer
    {
        __Ownable_init();
        clearinghouse = _clearinghouse;
        verifier = _verifier;
    }

    address internal clearinghouse;

    address internal verifier;

    // submitted withdrawal idxs
    mapping(uint64 => bool) public markedIdxs;

    // collected withdrawal fees in native token decimals
    mapping(uint32 => int128) public fees;

    uint64 public minIdx;

```

**File:** core/contracts/BaseProxyManager.sol (L74-88)
```text
abstract contract BaseProxyManager is OwnableUpgradeable {
    string internal constant CLEARINGHOUSE = "Clearinghouse";
    string internal constant CLEARINGHOUSE_LIQ = "ClearinghouseLiq";
    string internal constant ENDPOINT = "Endpoint";
    string internal constant ENDPOINT_TX = "EndpointTx";

    address public submitter;
    ProxyManagerHelper internal proxyManagerHelper;

    string[] internal contractNames;
    mapping(string => address) public proxies;
    mapping(string => address) public pendingImpls;
    mapping(string => bytes32) public pendingHashes;
    mapping(string => bytes32) public codeHashes;

```

**File:** core/contracts/BaseProxyManager.sol (L126-130)
```text
    function bootstrapEndpointTx(address _endpointTx, bytes32 expectedCodeHash)
        external
        onlyOwner
        reinitializer(2)
    {
```
