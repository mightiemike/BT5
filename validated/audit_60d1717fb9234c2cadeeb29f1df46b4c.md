### Title
Missing Storage Gaps in Multi-Level Upgradeable Parent Contracts Enables Critical Storage Corruption on Upgrade — (`BaseEngine.sol`, `EndpointGated.sol`, `ClearinghouseStorage.sol`, `EndpointStorage.sol`)

---

### Summary

Every intermediate parent contract in Nado's upgradeable inheritance chains lacks a `__gap` storage reservation. Adding a single state variable to any of these parents during a future upgrade will shift the storage layout of all child contracts, silently corrupting user balances, product states, and subaccount data.

---

### Finding Description

The Nado protocol deploys its core contracts behind ERC1967 proxies and uses OpenZeppelin's `initializer`/`__Ownable_init()` pattern throughout. The following intermediate parent contracts define state variables but declare no `__gap`:

**`EndpointGated`** — defines `address private endpoint` with no `__gap`: [1](#0-0) 

**`BaseEngine`** — defines four state variables (`_clearinghouse`, `productIds`, `canApplyDeltas`, `nonZeroBalances`) with no `__gap`: [2](#0-1) 

**`SpotEngineState`** — defines four critical balance/state mappings (`configs`, `states`, `balances`, `nlpLockedBalanceQueues`) with no `__gap`, sitting directly above `BaseEngine` in the chain: [3](#0-2) 

**`PerpEngineState`** — defines `states`, `balances`, `migrationFlag` with no `__gap`: [4](#0-3) 

**`ClearinghouseStorage`** — defines nine state variables (`quote`, `clearinghouse`, `clearinghouseLiq`, `productToEngine`, `engineByType`, `supportedEngines`, `insurance`, `lastLiquidationFees`, `spreads`, `withdrawPool`) with no `__gap`: [5](#0-4) 

**`EndpointStorage`** — defines all Endpoint state variables (`clearinghouse`, `spotEngine`, `perpEngine`, `sanctions`, `sequencer`, `subaccountIds`, `nonces`, `linkedSigners`, `nlpSigners`, `nlpPools`, etc.) with no `__gap`: [6](#0-5) 

The affected inheritance chains are:

```
SpotEngine → SpotEngineState → BaseEngine → EndpointGated → OwnableUpgradeable (has __gap)
PerpEngine → PerpEngineState → BaseEngine → EndpointGated → OwnableUpgradeable (has __gap)
Clearinghouse → EndpointGated + ClearinghouseStorage
ClearinghouseLiq → EndpointGated + ClearinghouseStorage   (used via delegatecall from Clearinghouse)
Endpoint → EIP712Upgradeable + OwnableUpgradeable + EndpointStorage
EndpointTx → EIP712Upgradeable + OwnableUpgradeable + EndpointStorage  (used via delegatecall from Endpoint)
```

`SpotEngine` and `PerpEngine` are initialized via `_initialize` in `BaseEngine`, confirming they are live upgradeable contracts: [7](#0-6) 

`Clearinghouse` is initialized with `initializer`: [8](#0-7) 

`Endpoint` is initialized with `initializer`: [9](#0-8) 

---

### Impact Explanation

**Highest-severity case — `BaseEngine` without `__gap`:**

The current storage layout for `SpotEngine` (simplified, after `OwnableUpgradeable`'s `__gap[49]`) is:

| Slot (relative) | Variable |
|---|---|
| 0 | `EndpointGated.endpoint` |
| 1 | `BaseEngine._clearinghouse` |
| 2 | `BaseEngine.productIds` |
| 3 | `BaseEngine.canApplyDeltas` |
| 4 | `BaseEngine.nonZeroBalances` |
| 5 | `SpotEngineState.configs` |
| 6 | `SpotEngineState.states` |
| 7 | `SpotEngineState.balances` |
| 8 | `SpotEngineState.nlpLockedBalanceQueues` |

If a developer adds one new variable to `BaseEngine` during an upgrade, every slot from `SpotEngineState.configs` onward shifts by one. The `balances` mapping — which stores every user's normalized collateral balance — now reads from the slot previously occupied by `states`, and vice versa. All subsequent reads and writes to user balances, product states, and NLP locked balance queues operate on wrong storage slots.

Concrete corrupted state:
- `balances[productId][subaccount]` in `SpotEngineState` → wrong slot → all user collateral balances return garbage or zero
- `states[productId]` in `SpotEngineState` → wrong slot → interest accrual (`cumulativeDepositsMultiplierX18`, `cumulativeBorrowsMultiplierX18`) corrupted
- `balances[productId][subaccount]` in `PerpEngineState` → wrong slot → all perp positions corrupted

The same class of corruption applies to `ClearinghouseStorage` (corrupts `insurance`, `productToEngine`, `spreads`) and `EndpointStorage` (corrupts `linkedSigners`, `nonces`, `subaccountIds`).

---

### Likelihood Explanation

**Medium.** The protocol is actively deployed on Ink Chain and is under ongoing development. The `upgradeClearinghouseLiq` and `upgradeEndpointTx` functions confirm that upgrades to logic contracts are a routine operational action: [10](#0-9) [11](#0-10) 

Any developer adding a new feature variable to `BaseEngine`, `EndpointGated`, `ClearinghouseStorage`, or `EndpointStorage` — a natural evolution for a growing protocol — will trigger this corruption without any explicit error or revert. The corruption is silent and only manifests as incorrect balance reads/writes after the upgrade.

---

### Recommendation

Add a `__gap` array at the end of every upgradeable parent contract's state variable declarations. The gap size should be chosen so that the total number of used + reserved slots equals a round number (e.g., 50):

```solidity
// In EndpointGated (currently uses 1 slot)
uint256[49] private __gap;

// In BaseEngine (currently uses 4 slots for state vars)
uint256[46] private __gap;

// In SpotEngineState (currently uses 4 slots)
uint256[46] private __gap;

// In PerpEngineState (currently uses 3 slots)
uint256[47] private __gap;

// In ClearinghouseStorage (currently uses ~9 slots)
uint256[41] private __gap;

// In EndpointStorage (currently uses ~17 slots)
uint256[33] private __gap;
```

Each time a new variable is added to a parent contract, decrement the corresponding `__gap` size by the number of slots consumed. This ensures the total storage footprint of the parent contract remains constant across upgrades, preserving the storage layout of all child contracts.

Additionally, consider using OpenZeppelin's `openzeppelin-contracts-upgradeable` consistently for all base contracts, as those already include `__gap` by design.

---

### Proof of Concept

1. Deploy `SpotEngine` behind an ERC1967 proxy. Record the proxy address.
2. User deposits collateral; `balances[productId][subaccount].amountNormalized` is written to storage slot 7 (relative).
3. Developer upgrades `BaseEngine` to add `address internal newFeatureAddr` as a fifth state variable.
4. After upgrade, `SpotEngineState.configs` now occupies slot 6, `states` occupies slot 7, `balances` occupies slot 8, `nlpLockedBalanceQueues` occupies slot 9.
5. Call `SpotEngine.getBalance(productId, subaccount)` — it reads from the new slot 8, which previously held `nlpLockedBalanceQueues` data (or is zero for a fresh slot), returning an incorrect balance.
6. `Clearinghouse.getHealth(subaccount, INITIAL)` calls `spotEngine.getHealthContribution(...)`, which iterates `nonZeroBalances` (now at the wrong slot) and reads `balances` from the wrong slot — returning a corrupted health value.
7. A user whose actual balance is positive may now appear to have zero or negative health, blocking withdrawals. Conversely, a user with zero balance may appear healthy, enabling unauthorized withdrawals.

### Citations

**File:** core/contracts/EndpointGated.sol (L10-11)
```text
abstract contract EndpointGated is OwnableUpgradeable, IEndpointGated {
    address private endpoint;
```

**File:** core/contracts/BaseEngine.sol (L15-26)
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

**File:** core/contracts/BaseEngine.sol (L203-218)
```text
    function _initialize(
        address _clearinghouseAddr,
        address _offchainExchangeAddr,
        address _endpointAddr,
        address _admin
    ) internal initializer {
        __Ownable_init();
        setEndpoint(_endpointAddr);
        transferOwnership(_admin);

        _clearinghouse = IClearinghouse(_clearinghouseAddr);

        canApplyDeltas[_endpointAddr] = true;
        canApplyDeltas[_clearinghouseAddr] = true;
        canApplyDeltas[_offchainExchangeAddr] = true;
    }
```

**File:** core/contracts/SpotEngineState.sol (L7-14)
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

**File:** core/contracts/Clearinghouse.sol (L677-684)
```text
    function upgradeClearinghouseLiq(address _clearinghouseLiq) external {
        require(
            msg.sender ==
                IProxyManager(_getProxyManager()).getProxyManagerHelper(),
            ERR_UNAUTHORIZED
        );
        clearinghouseLiq = _clearinghouseLiq;
    }
```

**File:** core/contracts/Endpoint.sol (L31-66)
```text
    function initialize(
        address _sanctions,
        address _sequencer,
        address _offchainExchange,
        IClearinghouse _clearinghouse,
        address _verifier,
        address _endpointTx
    ) external initializer {
        __Ownable_init();
        __EIP712_init("Nado", "0.0.1");
        sequencer = _sequencer;
        clearinghouse = _clearinghouse;
        offchainExchange = _offchainExchange;
        verifier = IVerifier(_verifier);
        sanctions = ISanctionsList(_sanctions);
        endpointTx = _endpointTx;
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
        slowModeConfig = SlowModeConfig({timeout: 0, txCount: 0, txUpTo: 0});
        priceX18[QUOTE_PRODUCT_ID] = ONE;

        if (nlpPools.length == 0) {
            nlpPools.push(
                NlpPool({
                    poolId: 0,
                    subaccount: N_ACCOUNT,
                    owner: address(0),
                    balanceWeightX18: uint128(ONE)
                })
            );
        }
    }
```

**File:** core/contracts/Endpoint.sol (L368-375)
```text
    function upgradeEndpointTx(address _endpointTx) external {
        require(
            msg.sender ==
                IProxyManager(_getProxyManager()).getProxyManagerHelper(),
            ERR_UNAUTHORIZED
        );
        endpointTx = _endpointTx;
    }
```
