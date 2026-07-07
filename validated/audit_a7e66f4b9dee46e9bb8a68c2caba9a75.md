### Title
Unprotected `initialize()` in `SpotEngine` Allows Frontrunner to Seize Ownership and Corrupt `canApplyDeltas` — (`core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.initialize()` is declared `external` with no access-control modifier. The `initializer` guard lives only on the internal `BaseEngine._initialize()`, which prevents a *second* call but does nothing to stop an attacker from being the *first* caller. Any unprivileged address can monitor the mempool, frontrun the legitimate deployment transaction, and become the owner of `SpotEngine` while injecting arbitrary addresses into `canApplyDeltas`, `_clearinghouse`, and `endpoint`.

---

### Finding Description

`SpotEngine.initialize()` is the public-facing entry point for engine setup:

```solidity
// core/contracts/SpotEngine.sol  lines 14-50
function initialize(
    address _clearinghouse,
    address _offchainExchange,
    address _quote,
    address _endpoint,
    address _admin
) external {                          // ← no access control
    _initialize(_clearinghouse, _offchainExchange, _endpoint, _admin);
    ...
}
``` [1](#0-0) 

The `initializer` modifier is placed on the internal `BaseEngine._initialize()`:

```solidity
// core/contracts/BaseEngine.sol  lines 203-218
function _initialize(
    address _clearinghouseAddr,
    address _offchainExchangeAddr,
    address _endpointAddr,
    address _admin
) internal initializer {
    __Ownable_init();
    setEndpoint(_endpointAddr);
    transferOwnership(_admin);          // ← _admin becomes owner
    _clearinghouse = IClearinghouse(_clearinghouseAddr);
    canApplyDeltas[_endpointAddr]       = true;
    canApplyDeltas[_clearinghouseAddr]  = true;
    canApplyDeltas[_offchainExchangeAddr] = true;
}
``` [2](#0-1) 

The `initializer` modifier only prevents a *second* invocation; it does not restrict *who* may be the first caller. Because `SpotEngine.initialize()` carries no `onlyOwner`, `onlyDeployer`, or equivalent guard, any EOA or contract can call it before the legitimate deployer.

Contrast this with every other upgradeable contract in the codebase, which either places `initializer` directly on the public function or adds `_disableInitializers()` in the constructor:

- `Endpoint.initialize()` — `external initializer` [3](#0-2) 
- `Clearinghouse.initialize()` — `external initializer` [4](#0-3) 
- `OffchainExchange.initialize()` — `external initializer` [5](#0-4) 
- `Verifier` — `_disableInitializers()` in constructor [6](#0-5) 

`SpotEngine` has neither protection on its public surface.

The legitimate initialization path is `Clearinghouse.addEngine()` (owner-only), which calls `productEngine.initialize(...)` internally:

```solidity
// core/contracts/Clearinghouse.sol  lines 156-181
function addEngine(...) external onlyOwner {
    ...
    productEngine.initialize(
        address(this), offchainExchange, quote, getEndpoint(), owner()
    );
}
``` [7](#0-6) 

An attacker who calls `SpotEngine.initialize()` directly before `addEngine()` is executed will:
1. Become the `owner` of `SpotEngine` (via `transferOwnership(_admin)` with attacker-supplied `_admin`).
2. Populate `canApplyDeltas` with attacker-controlled addresses, granting them the ability to call `updateBalance()` on any subaccount.
3. Set `_clearinghouse` and `endpoint` to attacker-controlled contracts.
4. Cause the subsequent legitimate `addEngine()` call to revert (because `_initialize()` will reject a second invocation), blocking protocol launch.

---

### Impact Explanation

**Owner of SpotEngine** — The owner can call `addOrUpdateProduct()` and `updateRisk()`, directly manipulating collateral weights and product configurations for all subaccounts. [8](#0-7) 

**`canApplyDeltas` corruption** — `updateBalance()` is gated by `_assertInternal()`, which checks `canApplyDeltas[msg.sender]`. With attacker-controlled entries in this mapping, the attacker's contracts can freely credit or debit any subaccount's balance. [9](#0-8) 

**Protocol launch blocked** — The legitimate `Clearinghouse.addEngine()` call will revert because `_initialize()` has already been consumed, forcing a full re-deploy of `SpotEngine` (and any dependent contracts already configured to reference it).

---

### Likelihood Explanation

The attack window opens the moment the `SpotEngine` proxy is deployed and closes when `Clearinghouse.addEngine()` is mined. On a public mempool (Ink Chain is EVM-compatible L2), an attacker can observe the deployment transaction and submit a direct call to `SpotEngine.initialize()` with a higher gas price. No special privileges, leaked keys, or social engineering are required — only the ability to submit a transaction.

---

### Recommendation

Add the `initializer` modifier directly to `SpotEngine.initialize()` (and the equivalent function in `PerpEngine`), or restrict the caller to the deployer/owner:

```solidity
// Option A – mirror every other upgradeable contract in the codebase
function initialize(...) external initializer {
    _initialize(...);
    ...
}

// Option B – add _disableInitializers() in a constructor
/// @custom:oz-upgrades-unsafe-allow constructor
constructor() {
    _disableInitializers();
}
```

Remove the `initializer` modifier from `BaseEngine._initialize()` once it is placed on the public function, to avoid double-guarding.

---

### Proof of Concept

1. Deployer broadcasts a transaction to deploy the `SpotEngine` proxy (or to call `Clearinghouse.addEngine(spotEngineProxy, ...)`).
2. Attacker observes the pending transaction in the mempool.
3. Attacker submits `SpotEngine(proxy).initialize(attackerClearinghouse, attackerExchange, anyQuote, attackerEndpoint, attackerAddress)` with higher gas.
4. Attacker's transaction is mined first. `_initialize()` runs: `transferOwnership(attackerAddress)` executes, `canApplyDeltas[attackerEndpoint] = true`, etc.
5. Deployer's `addEngine()` transaction is mined next. `productEngine.initialize(...)` reverts because `_initialize()` has already set the `Initializable` flag.
6. Attacker is now the owner of `SpotEngine` and controls `canApplyDeltas`. Protocol must redeploy.

### Citations

**File:** core/contracts/SpotEngine.sol (L14-22)
```text
    function initialize(
        address _clearinghouse,
        address _offchainExchange,
        address _quote,
        address _endpoint,
        address _admin
    ) external {
        _initialize(_clearinghouse, _offchainExchange, _endpoint, _admin);

```

**File:** core/contracts/SpotEngine.sol (L68-97)
```text
    function addOrUpdateProduct(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            quoteId,
            sizeIncrement,
            minSize,
            riskStore
        );
        configs[productId] = config;

        if (isNewProduct) {
            require(productId != QUOTE_PRODUCT_ID);
            _setState(
                productId,
                State({
                    cumulativeDepositsMultiplierX18: ONE,
                    cumulativeBorrowsMultiplierX18: ONE,
                    totalDepositsNormalized: 0,
                    totalBorrowsNormalized: 0
                })
            );
        }
    }
```

**File:** core/contracts/BaseEngine.sol (L199-201)
```text
    function _assertInternal() internal view virtual {
        require(canApplyDeltas[msg.sender], ERR_UNAUTHORIZED);
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

**File:** core/contracts/Endpoint.sol (L31-38)
```text
    function initialize(
        address _sanctions,
        address _sequencer,
        address _offchainExchange,
        IClearinghouse _clearinghouse,
        address _verifier,
        address _endpointTx
    ) external initializer {
```

**File:** core/contracts/Clearinghouse.sol (L25-31)
```text
    function initialize(
        address _endpoint,
        address _quote,
        address _clearinghouseLiq,
        uint256 _spreads,
        address _withdrawPool
    ) external initializer {
```

**File:** core/contracts/Clearinghouse.sol (L156-181)
```text
    function addEngine(
        address engine,
        address offchainExchange,
        IProductEngine.EngineType engineType
    ) external onlyOwner {
        require(address(engineByType[engineType]) == address(0));
        require(engine != address(0));
        IProductEngine productEngine = IProductEngine(engine);
        // Register
        supportedEngines.push(engineType);
        engineByType[engineType] = productEngine;

        // add quote to product mapping
        if (engineType == IProductEngine.EngineType.SPOT) {
            productToEngine[QUOTE_PRODUCT_ID] = productEngine;
        }

        // Initialize engine
        productEngine.initialize(
            address(this),
            offchainExchange,
            quote,
            getEndpoint(),
            owner()
        );
    }
```

**File:** core/contracts/OffchainExchange.sol (L243-247)
```text
    function initialize(address _clearinghouse, address _endpoint)
        external
        initializer
    {
        __Ownable_init();
```

**File:** core/contracts/Verifier.sol (L37-39)
```text
    constructor() {
        _disableInitializers();
    }
```
