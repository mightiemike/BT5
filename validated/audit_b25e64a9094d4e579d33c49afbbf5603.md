### Title
Missing Storage Gaps in Upgradeable Base Contracts Allow Storage Collision on Upgrade - (File: `core/contracts/EndpointGated.sol`, `core/contracts/BaseEngine.sol`)

### Summary
`EndpointGated` and `BaseEngine` are upgradeable abstract base contracts that hold storage variables and are each inherited by multiple upgradeable production contracts. Neither declares a `__gap` array. Any future addition of a storage variable to either base contract will shift the storage layout of every inheriting contract, silently corrupting all state variables in those contracts.

### Finding Description
`EndpointGated` is an upgradeable abstract contract (it inherits `OwnableUpgradeable`) that declares one storage slot:

```solidity
address private endpoint; // slot N
``` [1](#0-0) 

It is inherited by five upgradeable production contracts:

- `Clearinghouse is EndpointGated, ClearinghouseStorage`
- `ClearinghouseLiq is EndpointGated, ClearinghouseStorage`
- `OffchainExchange is IOffchainExchange, EndpointGated, EIP712Upgradeable`
- `SpotEngine` (via `SpotEngineState is ISpotEngine, BaseEngine` and `BaseEngine is IProductEngine, EndpointGated`)
- `PerpEngine` (via `PerpEngineState is IPerpEngine, BaseEngine`) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

`BaseEngine` is a second upgradeable abstract base (inheriting `EndpointGated`) that declares four additional storage slots:

```solidity
IClearinghouse internal _clearinghouse;
uint32[] internal productIds;
mapping(address => bool) internal canApplyDeltas;
mapping(bytes32 => mapping(uint32 => uint256)) internal nonZeroBalances;
``` [7](#0-6) 

Neither `EndpointGated` nor `BaseEngine` declares a `uint256[N] private __gap` reserve. A codebase-wide search confirms zero occurrences of `__gap` anywhere in the repository.

The concrete storage layout for `Clearinghouse` under the current proxy is (simplified):

| Slot | Variable |
|------|----------|
| 0 | `OwnableUpgradeable._owner` |
| 1 | `EndpointGated.endpoint` |
| 2 | `ClearinghouseStorage.quote` |
| 3 | `ClearinghouseStorage.clearinghouse` |
| … | … | [8](#0-7) 

If a new variable is inserted into `EndpointGated` after `endpoint`, every variable in `ClearinghouseStorage` (and the equivalent layouts for `ClearinghouseLiq`, `OffchainExchange`, `SpotEngine`, `PerpEngine`) shifts down by one slot. The proxy's stored data is not migrated, so every read and write after the upgrade operates on the wrong slot.

### Impact Explanation
A storage collision in `Clearinghouse` or `ClearinghouseLiq` would corrupt `insurance`, `clearinghouseLiq`, `engineByType`, and `productToEngine` mappings — the core accounting and routing state of the protocol. A collision in `SpotEngine` or `PerpEngine` would corrupt per-product `configs`, `states`, and `balances` mappings, enabling incorrect health calculations, wrong liquidation prices, and incorrect collateral accounting. A collision in `OffchainExchange` would corrupt `marketInfo`, `filledAmounts`, and `feeRates`, breaking order matching and fee collection. [9](#0-8) [10](#0-9) [11](#0-10) 

### Likelihood Explanation
The likelihood is medium. The vulnerability is not exploitable by an unprivileged caller today; it is triggered at the moment a protocol upgrade adds a new variable to `EndpointGated` or `BaseEngine`. Given that both contracts are actively developed base contracts in a live upgradeable system, and given that the protocol already has a `ProxyManager` / `BaseProxyManager` upgrade path, the probability of a future storage-extending upgrade is high. The absence of any `__gap` means there is no safe way to extend either base contract without a storage collision. [12](#0-11) 

### Recommendation
Add a `uint256[N] private __gap` array at the end of each upgradeable base contract's storage block, sized so that the total number of slots used by the contract plus the gap equals a round number (e.g., 50):

```solidity
// EndpointGated.sol — currently uses 1 slot (endpoint)
uint256[49] private __gap;

// BaseEngine.sol — currently uses 4 slots
uint256[46] private __gap;

// BaseWithdrawPool.sol — currently uses 5 slots
uint256[45] private __gap;

// ClearinghouseStorage.sol — currently uses ~9 slots
uint256[41] private __gap;
``` [13](#0-12) 

### Proof of Concept
1. Deploy `Clearinghouse` behind a `TransparentUpgradeableProxy`. Record the value of `ClearinghouseStorage.quote` (slot 2 under the current layout).
2. Upgrade `EndpointGated` to a new implementation that adds `address private newVar` after `endpoint`.
3. Upgrade `Clearinghouse` to the new implementation.
4. Read `ClearinghouseStorage.quote` — it now reads from slot 3, which previously held `ClearinghouseStorage.clearinghouse`. The `quote` address is now the `clearinghouse` address, and all downstream calls to `getQuote()` return the wrong value. Every subsequent deposit, withdrawal, and health check operates against the wrong token address. [14](#0-13)

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

**File:** core/contracts/Clearinghouse.sol (L25-40)
```text
    function initialize(
        address _endpoint,
        address _quote,
        address _clearinghouseLiq,
        uint256 _spreads,
        address _withdrawPool
    ) external initializer {
        __Ownable_init();
        setEndpoint(_endpoint);
        quote = _quote;
        clearinghouse = address(this);
        clearinghouseLiq = _clearinghouseLiq;
        spreads = _spreads;
        withdrawPool = _withdrawPool;
        emit ClearinghouseInitialized(_endpoint, _quote);
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L19-23)
```text
contract ClearinghouseLiq is
    EndpointGated,
    ClearinghouseStorage,
    IClearinghouseLiq
{
```

**File:** core/contracts/OffchainExchange.sol (L20-24)
```text
contract OffchainExchange is
    IOffchainExchange,
    EndpointGated,
    EIP712Upgradeable
{
```

**File:** core/contracts/OffchainExchange.sol (L26-49)
```text
    IClearinghouse internal clearinghouse;

    mapping(uint32 => MarketInfoStore) internal marketInfo;

    mapping(bytes32 => int128) public filledAmounts;

    ISpotEngine internal spotEngine;
    IPerpEngine internal perpEngine;

    // tier -> productId -> fee rates
    mapping(uint32 => mapping(uint32 => FeeRates)) internal feeRates;

    // address -> fee tiers
    mapping(address => uint32) internal feeTiers;
    mapping(address => bool) internal addressTouched;
    address[] internal customFeeAddresses;

    mapping(uint32 => uint32) internal quoteIds;

    // address -> mask (if the i-th bit is 1, it means the i-th iso subacc is being used)
    mapping(address => uint256) internal isolatedSubaccountsMask;

    // isolated subaccount -> subaccount
    mapping(bytes32 => bytes32) internal parentSubaccounts;
```

**File:** core/contracts/SpotEngineState.sol (L7-7)
```text
abstract contract SpotEngineState is ISpotEngine, BaseEngine {
```

**File:** core/contracts/SpotEngineState.sol (L10-13)
```text
    mapping(uint32 => Config) internal configs;
    mapping(uint32 => State) internal states;
    mapping(uint32 => mapping(bytes32 => BalanceNormalized)) internal balances;
    mapping(bytes32 => NlpLockedBalanceQueue) internal nlpLockedBalanceQueues;
```

**File:** core/contracts/PerpEngineState.sol (L13-13)
```text
abstract contract PerpEngineState is IPerpEngine, BaseEngine {
```

**File:** core/contracts/BaseEngine.sol (L18-24)
```text
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

**File:** core/contracts/BaseProxyManager.sol (L74-85)
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
```

**File:** core/contracts/BaseWithdrawPool.sol (L32-42)
```text
    address internal clearinghouse;

    address internal verifier;

    // submitted withdrawal idxs
    mapping(uint64 => bool) public markedIdxs;

    // collected withdrawal fees in native token decimals
    mapping(uint32 => int128) public fees;

    uint64 public minIdx;
```
