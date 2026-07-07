### Title
Unprotected `initialize()` in `SpotEngine` and `PerpEngine` Allows Front-Running of Engine Initialization — (File: `core/contracts/SpotEngine.sol`, `core/contracts/PerpEngine.sol`)

---

### Summary
`SpotEngine.initialize()` and `PerpEngine.initialize()` are `external` functions with no access control modifier. Any unprivileged caller can invoke them directly on a freshly deployed proxy before the legitimate `Clearinghouse.addEngine()` call executes. An attacker who front-runs the initialization can set themselves as `_admin` (owner) and populate `canApplyDeltas` with attacker-controlled addresses, permanently locking out the legitimate deployment and gaining privileged control over the engine.

---

### Finding Description

`SpotEngine.initialize()` is declared `external` with no `onlyOwner`, `onlyDeployer`, or equivalent guard: [1](#0-0) 

`PerpEngine.initialize()` follows the identical pattern: [2](#0-1) 

Both delegate to `BaseEngine._initialize()`, which carries the `initializer` modifier: [3](#0-2) 

The `initializer` modifier (OpenZeppelin `Initializable`) sets `_initialized = 1` on the **first** call and reverts on every subsequent call. It does **not** restrict *who* may be the first caller. Because neither `SpotEngine` nor `PerpEngine` contains a constructor that calls `_disableInitializers()`, the proxy's initialization slot is open to any external caller from the moment the proxy is deployed until `Clearinghouse.addEngine()` is executed.

The legitimate initialization path is: [4](#0-3) 

`addEngine()` is `onlyOwner`, so the attacker cannot call it. However, the attacker can call `SpotEngine.initialize()` (or `PerpEngine.initialize()`) directly on the proxy address before the owner submits the `addEngine()` transaction. Once the attacker's call lands, `_initialized = 1` is set in the proxy's storage. The subsequent `addEngine()` call reverts inside `productEngine.initialize()`, and the engine is never registered in the clearinghouse.

---

### Impact Explanation

**State delta corrupted:** `_initialized`, `owner`, `_clearinghouse`, `canApplyDeltas` in the engine proxy's storage are all set by the attacker.

Concrete consequences:

1. **Deployment DoS** — `Clearinghouse.addEngine()` reverts because `_initialize()` rejects a second call. The engine is never registered; `engineByType[SPOT/PERP]` and `productToEngine` remain `address(0)`. The entire protocol cannot be brought live without redeploying the engine proxy.

2. **Attacker becomes engine owner** — `transferOwnership(_admin)` is called with the attacker's address. The attacker can call `onlyOwner` functions: `addOrUpdateProduct()` (sets risk weights and token configs) and `updateRisk()` (overrides collateral weights), directly corrupting health calculations for all subaccounts.

3. **Attacker controls `canApplyDeltas`** — `canApplyDeltas[attacker] = true` is written for all three attacker-supplied addresses. `_assertInternal()` (the only guard on `updateBalance()`) passes for the attacker, allowing arbitrary balance mutations on any subaccount. [5](#0-4) 

---

### Likelihood Explanation

The attack window opens the moment the engine proxy is deployed and closes when `Clearinghouse.addEngine()` is mined. Because `ContractOwner` and the deployment tasks submit these as separate transactions, the window is observable in the public mempool. A front-running bot monitoring for proxy deployments whose bytecode matches `SpotEngine`/`PerpEngine` can reliably execute this attack with a higher gas price. No special privilege, leaked key, or social engineering is required — only the ability to submit a transaction.

---

### Recommendation

**Short term:** Add an access control check to `SpotEngine.initialize()` and `PerpEngine.initialize()` so only the deployer or the clearinghouse can call them, e.g.:

```solidity
function initialize(...) external {
    require(msg.sender == expectedDeployer, "unauthorized");
    _initialize(...);
}
```

Alternatively, call `_disableInitializers()` in the implementation constructors (as `BaseWithdrawPool`, `Airdrop`, `BaseProxyManager`, `ContractOwner`, and `Verifier` already do) and initialize exclusively through the proxy factory in a single atomic transaction. [6](#0-5) 

**Long term:** Audit all `initialize()` functions across the codebase for missing `

### Citations

**File:** core/contracts/SpotEngine.sol (L14-21)
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

**File:** core/contracts/BaseWithdrawPool.sol (L18-21)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```
