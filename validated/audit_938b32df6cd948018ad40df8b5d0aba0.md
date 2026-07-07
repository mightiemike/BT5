### Title
Missing `initializer` Modifier Allows Re-initialization of `SpotEngine` and `PerpEngine` — (`File: core/contracts/SpotEngine.sol`, `core/contracts/PerpEngine.sol`)

---

### Summary

`SpotEngine.initialize()` and `PerpEngine.initialize()` are `external` functions with no access control and no OpenZeppelin `initializer` modifier. Any caller can invoke them after deployment to overwrite critical state — including the clearinghouse address, endpoint address, and contract owner — effectively seizing control of both engines.

---

### Finding Description

Every other upgradeable contract in the protocol (`Endpoint`, `Clearinghouse`, `OffchainExchange`, `Verifier`, `Airdrop`, `ContractOwner`, `BaseProxyManager`) guards its `initialize()` with the OpenZeppelin `initializer` modifier, which sets a version counter and prevents re-entry. `SpotEngine` and `PerpEngine` are the sole exceptions.

`SpotEngine.initialize()`:

```solidity
function initialize(
    address _clearinghouse,
    address _offchainExchange,
    address _quote,
    address _endpoint,
    address _admin
) external {                          // ← no `initializer`, no access control
    _initialize(_clearinghouse, _offchainExchange, _endpoint, _admin);
    ...
}
``` [1](#0-0) 

`PerpEngine.initialize()`:

```solidity
function initialize(
    address _clearinghouse,
    address _offchainExchange,
    address,
    address _endpoint,
    address _admin
) external {                          // ← no `initializer`, no access control
    _initialize(_clearinghouse, _offchainExchange, _endpoint, _admin);
}
``` [2](#0-1) 

Because the `initializer` modifier is absent, the OpenZeppelin `Initializable` state machine is never engaged. The `_initialize()` call inherited from `BaseEngine` must therefore reach `_transferOwnership()` directly (rather than through `__Ownable_init()`, which carries an `onlyInitializing` guard that would revert on a non-initializing call — and would also have prevented the *first* legitimate call from `Clearinghouse.addEngine()`). This means `_initialize()` unconditionally overwrites the stored `clearinghouse`, `offchainExchange`, `endpoint`, and owner on every invocation.

The legitimate initialization path is `Clearinghouse.addEngine()` (owner-only), which calls `productEngine.initialize(...)`. That call succeeds, but it leaves the engine's `initialize()` permanently open to any external caller. [3](#0-2) 

---

### Impact Explanation

An attacker who calls `SpotEngine.initialize(attacker_clearinghouse, attacker_offchainExchange, ...)` after deployment:

1. **Becomes owner** of `SpotEngine` / `PerpEngine` — gains unrestricted access to `addOrUpdateProduct()` (owner-only), allowing arbitrary manipulation of risk parameters, token addresses, and size increments for every listed market.
2. **Replaces the clearinghouse pointer** — `_assertInternal()` in `BaseEngine` gates all balance mutations through the stored clearinghouse address. Replacing it with an attacker-controlled contract bypasses every balance-update guard, enabling arbitrary credit/debit of any subaccount.
3. **Replaces the endpoint pointer** — breaks the `onlyEndpoint` modifier across `Clearinghouse`, `OffchainExchange`, and the engines, allowing the attacker to submit privileged transactions directly.

The net result is complete accounting corruption: arbitrary minting of spot/perp balances, theft of deposited collateral, and permanent disruption of the settlement system.

---

### Likelihood Explanation

The function is `external` with zero access control and zero initialization guard. Any EOA or contract can call it at any time after deployment. No special privilege, leaked key, or governance capture is required. The attack is a single transaction.

---

### Recommendation

Add the `initializer` modifier (or `reinitializer(n)` if a version bump is needed) to both `SpotEngine.initialize()` and `PerpEngine.initialize()`, consistent with every other upgradeable contract in the protocol:

```solidity
function initialize(...) external initializer {
    _initialize(...);
    ...
}
```

Additionally, add a constructor with `_disableInitializers()` to both contracts (as already done in `ContractOwner`, `Verifier`, `Airdrop`, and `BaseProxyManager`) to prevent initialization of the bare implementation contract. [4](#0-3) 

---

### Proof of Concept

1. Protocol deploys `SpotEngine` and `Clearinghouse`. `Clearinghouse.addEngine()` calls `SpotEngine.initialize(clearinghouse, offchainExchange, quote, endpoint, multisig)` — succeeds, sets owner to `multisig`.
2. Attacker deploys `MaliciousClearinghouse` that approves all `updateBalance` calls.
3. Attacker calls:
   ```solidity
   SpotEngine(spotEngineAddr).initialize(
       address(maliciousClearinghouse),
       address(maliciousOffchainExchange),
       quoteToken,
       address(maliciousEndpoint),
       attacker
   );
   ```
4. `SpotEngine` owner is now `attacker`. `_clearinghouse` is now `maliciousClearinghouse`.
5. Attacker calls `SpotEngine.updateBalance(QUOTE_PRODUCT_ID, victimSubaccount, -victimBalance)` routed through `maliciousClearinghouse` (which passes `_assertInternal()`), draining the victim's quote balance.
6. Attacker calls `SpotEngine.addOrUpdateProduct(...)` to set a zero-weight risk store for any product, bypassing health checks for subsequent withdrawals. [5](#0-4) [2](#0-1)

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

**File:** core/contracts/ContractOwner.sol (L43-46)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```
