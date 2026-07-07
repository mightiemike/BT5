### Title
Unguarded `initialize()` Functions Allow Any Caller to Seize Engine Ownership and Corrupt `canApplyDeltas` — (`File: core/contracts/SpotEngine.sol`, `core/contracts/PerpEngine.sol`)

---

### Summary

`SpotEngine.initialize()` and `PerpEngine.initialize()` are `external` functions with no `initializer` modifier and no access control. The `initializer` guard exists only on the internal `_initialize()` in `BaseEngine`, meaning the first external caller — not necessarily the deployer — wins ownership and sets all critical protocol addresses. Neither engine contract has a constructor calling `_disableInitializers()`, unlike `ContractOwner` and `Verifier` which both do.

---

### Finding Description

`SpotEngine.initialize()` is declared `external` with no modifier and no caller check: [1](#0-0) 

`PerpEngine.initialize()` is identical in structure: [2](#0-1) 

Both delegate to `BaseEngine._initialize()`, which carries the `initializer` modifier: [3](#0-2) 

The `initializer` modifier on `_initialize()` prevents a *second* call, but it does not prevent an *unauthorized first call*. Because neither `SpotEngine` nor `PerpEngine` (nor any of their base contracts `BaseEngine`, `SpotEngineState`, `PerpEngineState`) define a constructor calling `_disableInitializers()`, the implementation contract and any proxy whose `initialize()` has not yet been called are both open to front-running.

Contrast this with `ContractOwner`, which correctly combines `_disableInitializers()` in the constructor, the `initializer` modifier on `initialize()`, and a `require(_deployer == msg.sender)` caller check: [4](#0-3) 

`Verifier` similarly calls `_disableInitializers()` in its constructor: [5](#0-4) 

`SpotEngine` and `PerpEngine` have none of these defenses.

---

### Impact Explanation

An attacker who front-runs `SpotEngine.initialize()` or `PerpEngine.initialize()` on the proxy (or initializes the bare implementation) gains:

1. **Ownership** of the engine (`transferOwnership(_admin)` is called with the attacker's address). [6](#0-5) 
2. **Full control of `canApplyDeltas`** — the mapping that gates every balance mutation (`updateBalance`, `settlePnl`, `socializeSubaccount`). The attacker supplies their own `_endpoint`, `_clearinghouse`, and `_offchainExchange` addresses, granting those addresses the right to arbitrarily credit or debit any subaccount's spot or perp balance. [7](#0-6) 
3. **Corrupt `_clearinghouse` pointer** — health checks, liquidations, and insurance fund operations all route through `_clearinghouse`. With a malicious address here, solvency invariants are broken. [8](#0-7) 
4. For `SpotEngine` specifically, the attacker also sets `configs[QUOTE_PRODUCT_ID].token` to an arbitrary address, redirecting all quote-token accounting. [9](#0-8) 

The result is complete compromise of all user spot and perp balances — equivalent to locking or stealing deposited collateral.

---

### Likelihood Explanation

The attack window is the gap between proxy deployment and the deployer's initialization transaction. This gap is observable on-chain. A mempool-watching attacker can submit `SpotEngine.initialize(attacker, attacker, attacker_quote, attacker_endpoint, attacker)` with higher gas and win the race. No special privilege is required — the function is `external` with no guard whatsoever.

---

### Recommendation

Apply all three layers of protection used by `ContractOwner` and `Verifier`:

1. Add a constructor to `BaseEngine` (or each engine) that calls `_disableInitializers()` to lock the implementation contract.
2. Add the `initializer` modifier directly to `SpotEngine.initialize()` and `PerpEngine.initialize()` (not only to the internal `_initialize()`).
3. Add a caller check (e.g., `require(msg.sender == expectedDeployer)`) or restrict initialization to a known factory/deployer address.

---

### Proof of Concept

```solidity
// Attacker monitors mempool, sees SpotEngine proxy deployed but not yet initialized.
// Attacker submits with higher gas:

SpotEngine(proxyAddress).initialize(
    address(attackerClearinghouse),   // _clearinghouse
    address(attackerExchange),        // _offchainExchange
    address(attackerToken),           // _quote
    address(attackerEndpoint),        // _endpoint
    attacker                          // _admin → becomes owner
);

// Result:
// - attacker is now owner of SpotEngine
// - canApplyDeltas[attackerEndpoint] = true
// - canApplyDeltas[attackerClearinghouse] = true
// - canApplyDeltas[attackerExchange] = true
// - configs[QUOTE_PRODUCT_ID].token = attackerToken
// Deployer's subsequent initialize() call reverts (initializer already set).
// All user balance operations now route through attacker-controlled contracts.
```

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

**File:** core/contracts/SpotEngine.sol (L23-31)
```text
        configs[QUOTE_PRODUCT_ID] = Config({
            token: _quote,
            interestInflectionUtilX18: 8e17, // .8
            interestFloorX18: 1e16, // .01
            interestSmallCapX18: 4e16, // .04
            interestLargeCapX18: ONE, // 1
            withdrawFeeX18: ONE, // 1
            minDepositRateX18: 0 // 0
        });
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

**File:** core/contracts/ContractOwner.sol (L43-58)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

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

**File:** core/contracts/Verifier.sol (L36-39)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```
