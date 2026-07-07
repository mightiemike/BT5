### Title
Unprotected `initialize()` on SpotEngine and PerpEngine Allows Front-Running to Grief Deployment or Steal Engine Ownership — (`core/contracts/SpotEngine.sol`, `core/contracts/PerpEngine.sol`)

---

### Summary

`SpotEngine.initialize()` and `PerpEngine.initialize()` are `external` functions with no access control and no `initializer` modifier on the public entry point. Any unprivileged caller can invoke them before the legitimate deployer, either griefing the deployment or seizing ownership of the engine contracts.

---

### Finding Description

Both engine contracts expose a public `initialize()` entry point that delegates to `BaseEngine._initialize()`, which carries the OpenZeppelin `initializer` modifier:

`SpotEngine.initialize()` — no modifier, no caller check: [1](#0-0) 

`PerpEngine.initialize()` — identical pattern: [2](#0-1) 

The `initializer` guard lives only on the internal `_initialize()`: [3](#0-2) 

This means the one-time guard exists, but there is **no restriction on who calls `initialize()` first**. Every other upgradeable contract in the system places the `initializer` modifier directly on its public entry point: [4](#0-3) [5](#0-4) 

The legitimate initialization path goes through `Clearinghouse.addEngine()`, which is `onlyOwner` and calls `productEngine.initialize(...)`: [6](#0-5) 

Between proxy deployment and the `addEngine()` call, there is an open window where any attacker can call `SpotEngine.initialize()` or `PerpEngine.initialize()` directly.

---

### Impact Explanation

**Scenario A — Griefing / forced redeployment:**
The attacker calls `SpotEngine.initialize()` with any parameters. The `initializer` flag is set. When the legitimate `addEngine()` call arrives, `_initialize()` reverts with "Initializable: contract is already initialized", causing `addEngine()` to revert. The deployer must redeploy the engine proxy.

**Scenario B — Ownership theft:**
The attacker calls `SpotEngine.initialize()` with the correct `_clearinghouse`, `_offchainExchange`, `_endpoint` addresses but substitutes their own address as `_admin`. `__Ownable_init()` + `transferOwnership(_admin)` inside `_initialize()` makes the attacker the owner of `SpotEngine`. The legitimate `addEngine()` then fails. If the deployment script does not detect the revert and proceeds (or if the attacker's initialization is accepted by a flawed script), the attacker-owned `SpotEngine` is wired into the protocol. The attacker can then call `addOrUpdateProduct()` (which is `onlyOwner`) to set arbitrary risk weights, token addresses, interest rates, and `withdrawFeeX18` values — directly corrupting health calculations and collateral valuations for all users. [7](#0-6) 

---

### Likelihood Explanation

The deployment of engine proxies and their initialization via `addEngine()` are separate transactions. On any public mempool chain, a bot watching for proxy deployments can immediately call `initialize()` on the newly deployed implementation or proxy before the deployer's `addEngine()` transaction is mined. No special privilege is required — the function is `external` with no guard.

---

### Recommendation

Add the `initializer` modifier directly to the public `initialize()` functions in `SpotEngine` and `PerpEngine`, matching the pattern used by every other upgradeable contract in the system:

```solidity
// SpotEngine.sol
function initialize(
    address _clearinghouse,
    address _offchainExchange,
    address _quote,
    address _endpoint,
    address _admin
) external initializer {   // <-- add initializer here
    ...
}
```

Alternatively, restrict the caller to a known deployer address (as `ContractOwner.initialize()` does with `require(_deployer == msg.sender, ...)`): [8](#0-7) 

---

### Proof of Concept

1. Deployer broadcasts a transaction to deploy the `SpotEngine` proxy.
2. Attacker observes the pending deployment in the mempool and submits a higher-gas transaction calling:
   ```solidity
   SpotEngine(proxyAddress).initialize(
       legitimateClearinghouse,
       legitimateOffchainExchange,
       legitimateQuote,
       legitimateEndpoint,
       attacker          // <-- attacker becomes owner
   );
   ```
3. Attacker's transaction is mined first. `_initialize()` runs, `transferOwnership(attacker)` executes.
4. Deployer's `Clearinghouse.addEngine(proxyAddress, ...)` is mined — `_initialize()` reverts ("already initialized") — `addEngine()` reverts entirely.
5. Deployer must redeploy. If the deployment script does not verify ownership post-initialization, the attacker-owned engine could be wired into the protocol, granting the attacker `onlyOwner` access to `addOrUpdateProduct()`, `updateRisk()`, and all other owner-gated engine functions. [9](#0-8) [2](#0-1)

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

**File:** core/contracts/ContractOwner.sol (L48-58)
```text
    function initialize(
        address multisig,
        address _deployer,
        address _spotEngine,
        address _perpEngine,
        address _endpoint,
        address _clearinghouse,
        address _verifier,
        address payable _wrappedNative
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
```
