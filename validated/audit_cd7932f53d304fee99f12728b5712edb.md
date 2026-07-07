### Title
Unprotected `initialize()` on `SpotEngine` and `PerpEngine` Allows Initialization DoS, Forcing Redeployment — (File: `core/contracts/SpotEngine.sol`, `core/contracts/PerpEngine.sol`)

---

### Summary

`SpotEngine.initialize()` and `PerpEngine.initialize()` are `external` functions with no access control. Any unprivileged caller can invoke them directly on the deployed proxy before the protocol's setup sequence runs. When the protocol owner subsequently calls `Clearinghouse.addEngine()`, which internally calls `productEngine.initialize()`, the call reverts because the contract is already initialized — forcing redeployment of the engine.

---

### Finding Description

Both `SpotEngine.initialize()` and `PerpEngine.initialize()` are declared `external` with no access control modifier:

```solidity
// SpotEngine.sol
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
```

```solidity
// PerpEngine.sol
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

The `initializer` guard lives only on the `internal` function `_initialize()` in `BaseEngine`:

```solidity
function _initialize(...) internal initializer {
    __Ownable_init();
    ...
}
```

Because `initializer` is on the `internal` helper rather than the `external` entry point, there is no caller restriction preventing an arbitrary address from triggering initialization first.

The protocol's intended setup path goes through `Clearinghouse.addEngine()`, which is `onlyOwner` and calls `productEngine.initialize()` as its final step:

```solidity
productEngine.initialize(
    address(this),
    offchainExchange,
    quote,
    getEndpoint(),
    owner()
);
```

If an attacker has already called `SpotEngine.initialize()` or `PerpEngine.initialize()` directly, the `initializer` modifier in `_initialize()` will revert with `"Initializable: contract is already initialized"`, causing `addEngine` to fail entirely.

---

### Impact Explanation

`Clearinghouse.addEngine()` is the sole mechanism for registering a product engine. If it reverts, `engineByType` is never populated, `productToEngine` is never set, and the protocol cannot process any trades, deposits, or health checks. The entire protocol is non-functional until the engine proxy is redeployed and the setup sequence is retried — identical in consequence to the external report's "initialization is dossed, forcing redeployment."

---

### Likelihood Explanation

The attack requires only that the attacker observe the engine proxy deployment on-chain and submit a direct call to `initialize()` before the owner calls `addEngine`. No special privileges, leaked keys, or governance capture are needed. Front-running a single transaction is straightforward on any EVM chain with a public mempool.

---

### Recommendation

Move the `initializer` modifier from `_initialize()` to the public `initialize()` functions in `SpotEngine` and `PerpEngine`, or add an explicit access-control check (e.g., `onlyOwner` or a deployer guard) to those `external` entry points. This mirrors the pattern already used in `Clearinghouse.initialize()`, `Endpoint.initialize()`, and `OffchainExchange.initialize()`, all of which carry the `initializer` modifier directly on the `external` function.

---

### Proof of Concept

1. Protocol deploys the `SpotEngine` proxy contract.
2. Attacker observes the deployment and calls `SpotEngine.initialize(attacker, attacker, attacker, attacker, attacker)` directly.
3. `_initialize()` executes under the `initializer` modifier, marking the proxy as initialized and setting the attacker as owner.
4. Protocol owner calls `Clearinghouse.addEngine(spotEngineAddr, offchainExchange, SPOT)`.
5. `addEngine` reaches `productEngine.initialize(...)` and reverts: `"Initializable: contract is already initialized"`.
6. `addEngine` is rolled back; `engineByType[SPOT]` is never set.
7. Protocol is non-functional; redeployment of the `SpotEngine` proxy is required.

The same sequence applies identically to `PerpEngine`.

---

**Affected locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** core/contracts/Clearinghouse.sol (L156-180)
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
```
