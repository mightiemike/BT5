### Title
Unguarded `initialize()` Functions Allow Unauthorized First-Caller Takeover - (`core/contracts/SpotEngine.sol`, `core/contracts/PerpEngine.sol`, `core/contracts/WithdrawPool.sol`)

---

### Summary

`SpotEngine.initialize()`, `PerpEngine.initialize()`, and `WithdrawPool.initialize()` are `external` functions with no caller restriction. OpenZeppelin's `initializer` modifier (applied in the internal `_initialize` delegate) prevents re-initialization but does not restrict *who* calls it first. An attacker who front-runs the deployer's initialization transaction permanently takes over these contracts, as the legitimate deployer cannot re-initialize.

---

### Finding Description

`SpotEngine.initialize()` and `PerpEngine.initialize()` are declared `external` with no access-control modifier and no deployer check:

```solidity
// SpotEngine.sol line 14
function initialize(
    address _clearinghouse,
    address _offchainExchange,
    address _quote,
    address _endpoint,
    address _admin
) external {
    _initialize(_clearinghouse, _offchainExchange, _endpoint, _admin);
    ...
}
``` [1](#0-0) 

`PerpEngine.initialize()` is identical in structure: [2](#0-1) 

`WithdrawPool.initialize()` follows the same pattern: [3](#0-2) 

The `initializer` guard is applied only inside `BaseEngine._initialize()`:

```solidity
function _initialize(...) internal initializer {
    __Ownable_init();
    setEndpoint(_endpointAddr);
    transferOwnership(_admin);
    _clearinghouse = IClearinghouse(_clearinghouseAddr);
    canApplyDeltas[_endpointAddr] = true;
    canApplyDeltas[_clearinghouseAddr] = true;
    canApplyDeltas[_offchainExchangeAddr] = true;
}
``` [4](#0-3) 

The `initializer` modifier prevents a *second* call but does not restrict the *first* caller. Neither `SpotEngine` nor `PerpEngine` nor their base classes call `_disableInitializers()` in a constructor, leaving both the proxy and the implementation contract open to unauthorized first initialization.

By contrast, contracts that correctly protect themselves call `_disableInitializers()` in their constructor (e.g., `Airdrop`, `Verifier`, `ContractOwner`, `BaseWithdrawPool`): [5](#0-4) 

`ContractOwner.initialize()` additionally enforces a deployer check: [6](#0-5) 

`SpotEngine` and `PerpEngine` have neither protection.

---

### Impact Explanation

An attacker who calls `SpotEngine.initialize()` first supplies:
- `_admin` = attacker address → becomes `owner` via `transferOwnership(_admin)`
- `_clearinghouse`, `_offchainExchange`, `_endpoint` = attacker-controlled contracts → `canApplyDeltas` is populated with attacker addresses

After this, the legitimate deployer's `initialize()` call reverts (blocked by `initializer`). The attacker-owned `SpotEngine` controls all spot balance accounting, deposit/withdrawal logic, and interest-rate state for the entire protocol. The `PerpEngine` analog corrupts all perpetual position accounting. The `WithdrawPool` analog redirects the `clearinghouse` and `verifier` references, breaking fast-withdrawal signature validation and allowing unauthorized fund transfers.

The corrupted state delta is: `owner`, `_clearinghouse`, `canApplyDeltas`, and `endpoint` storage slots in `SpotEngine`/`PerpEngine` are permanently set to attacker-controlled values with no recovery path short of full redeployment.

---

### Likelihood Explanation

The attack window is the block gap between proxy deployment and the deployer's `initialize()` call. On a public mempool chain, a bot monitoring for proxy deployments can front-run the initialization transaction with a higher gas price. The attack requires only a single transaction and no capital. The attacker does not need to sustain the exploit — a single successful front-run permanently bricks the contract.

---

### Recommendation

1. Add `_disableInitializers()` to a constructor in `BaseEngine` (and any other engine base) to prevent initialization of the implementation contract directly.
2. Add a deployer check to the public `initialize()` functions, or restrict them to `onlyOwner` / a factory address, consistent with the pattern already used in `ContractOwner.initialize()`:

```solidity
constructor() {
    _disableInitializers();
}

function initialize(..., address _admin) external {
    require(msg.sender == _admin, "only deployer can initialize");
    _initialize(...);
}
```

---

### Proof of Concept

1. Deployer broadcasts a transaction to deploy `SpotEngine` proxy.
2. Attacker observes the pending deployment in the mempool.
3. Attacker broadcasts `SpotEngine.initialize(maliciousClearinghouse, maliciousExchange, quote, maliciousEndpoint, attackerAddress)` with higher gas.
4. Attacker's transaction is mined first; `_initialize` runs, sets `owner = attackerAddress`, `canApplyDeltas[maliciousEndpoint] = true`, etc.
5. Deployer's subsequent `initialize()` call reverts: `Initializable: contract is already initialized`.
6. The entire Nado protocol deployment is broken; `SpotEngine` is permanently owned by the attacker. [7](#0-6) [8](#0-7) [2](#0-1)

### Citations

**File:** core/contracts/SpotEngine.sol (L14-50)
```text
    function initialize(
        address _clearinghouse,
        address _offchainExchange,
        address _quote,
        address _endpoint,
        address _admin
    ) external {
        _initialize(_clearinghouse, _offchainExchange, _endpoint, _admin);

        configs[QUOTE_PRODUCT_ID] = Config({
            token: _quote,
            interestInflectionUtilX18: 8e17, // .8
            interestFloorX18: 1e16, // .01
            interestSmallCapX18: 4e16, // .04
            interestLargeCapX18: ONE, // 1
            withdrawFeeX18: ONE, // 1
            minDepositRateX18: 0 // 0
        });
        _risk().value[QUOTE_PRODUCT_ID] = RiskHelper.RiskStore({
            longWeightInitial: 1e9,
            shortWeightInitial: 1e9,
            longWeightMaintenance: 1e9,
            shortWeightMaintenance: 1e9,
            priceX18: ONE
        });
        _setState(
            QUOTE_PRODUCT_ID,
            State({
                cumulativeDepositsMultiplierX18: ONE,
                cumulativeBorrowsMultiplierX18: ONE,
                totalDepositsNormalized: 0,
                totalBorrowsNormalized: 0
            })
        );
        productIds.push(QUOTE_PRODUCT_ID);
        emit AddOrUpdateProduct(QUOTE_PRODUCT_ID);
    }
```

**File:** core/contracts/PerpEngine.sol (L14-22)
```text
    function initialize(
        address _clearinghouse,
        address _offchainExchange,
        address,
        address _endpoint,
        address _admin
    ) external {
        _initialize(_clearinghouse, _offchainExchange, _endpoint, _admin);
    }
```

**File:** core/contracts/WithdrawPool.sol (L16-18)
```text
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
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

**File:** core/contracts/BaseWithdrawPool.sol (L18-21)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/ContractOwner.sol (L57-58)
```text
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
```
