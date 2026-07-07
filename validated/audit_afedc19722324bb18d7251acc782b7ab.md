### Title
Unprotected `initialize()` on `SpotEngine` and `PerpEngine` Allows Attacker to Seize Engine Ownership and Brick Deployment - (File: `core/contracts/SpotEngine.sol`, `core/contracts/PerpEngine.sol`)

---

### Summary

`SpotEngine.initialize()` and `PerpEngine.initialize()` are externally callable with no `msg.sender` restriction and no `_disableInitializers()` guard on the implementation constructor. Any attacker who calls either function before `Clearinghouse.addEngine()` is executed can inject malicious addresses for `_clearinghouse`, `_offchainExchange`, `_endpoint`, and `_admin`, seize ownership of the engine, and permanently prevent legitimate initialization.

---

### Finding Description

`SpotEngine.initialize()` is declared `external` with no access modifier and no caller check: [1](#0-0) 

`PerpEngine.initialize()` is identical in structure: [2](#0-1) 

The `initializer` guard lives only on the internal `BaseEngine._initialize()`: [3](#0-2) 

Neither `SpotEngine` nor `PerpEngine` calls `_disableInitializers()` in a constructor (unlike `Verifier`, `BaseProxyManager`, `ContractOwner`, and `Airdrop`, which all do). The legitimate initialization path is `Clearinghouse.addEngine()` → `productEngine.initialize(...)`: [4](#0-3) 

`addEngine()` is `onlyOwner`, meaning it is called in a separate transaction after the engine proxy is deployed. The window between proxy deployment and the `addEngine()` call is the attack surface.

An attacker who monitors the mempool can call `SpotEngine.initialize(attackerClearinghouse, attackerExchange, attackerQuote, attackerEndpoint, attacker)` directly on the proxy before `addEngine()` fires. Because `initializer` is a one-shot guard, the subsequent legitimate call from `addEngine()` will revert with "Initializable: contract is already initialized", permanently bricking the deployment.

The `_initialize()` call sets the `canApplyDeltas` whitelist: [5](#0-4) 

With attacker-controlled addresses in that whitelist, the attacker's contracts can call `updateBalance()` on the engine at will, corrupting every subaccount balance.

---

### Impact Explanation

If the attacker wins the race:

1. **Ownership seized**: `_admin` is passed to `transferOwnership()`, making the attacker the owner of `SpotEngine` and `PerpEngine`. The attacker can call `addOrUpdateProduct()` and `updateRisk()` to set arbitrary collateral weights, enabling undercollateralized borrowing or blocking all withdrawals.
2. **`canApplyDeltas` poisoned**: Attacker-controlled addresses are whitelisted to call `updateBalance()`, allowing direct manipulation of any subaccount's spot or perp balance — collateral theft or artificial insolvency.
3. **Deployment permanently bricked**: The `initializer` modifier prevents any subsequent legitimate call, so the protocol cannot recover without redeploying all contracts.

The corrupted state delta is: every `balances[productId][subaccount]` in `SpotEngine` and `PerpEngine` becomes attacker-writable, and `_risk().value[productId]` becomes attacker-configurable.

---

### Likelihood Explanation

The attack requires only a standard mempool front-run during deployment. Multi-step deployments (deploy proxy → call `addEngine()` in a later transaction) are the norm for complex protocols. No special privilege, leaked key, or social engineering is needed — only the ability to submit a transaction with a higher gas price before the deployer's `addEngine()` transaction is mined. This is straightforwardly executable on Ink Chain (an EVM-compatible chain).

---

### Recommendation

Apply one or both of the following:

1. **Add `_disableInitializers()` to the implementation constructor** of `SpotEngine` and `PerpEngine` (as already done in `Verifier`, `BaseProxyManager`, `ContractOwner`, and `Airdrop`), preventing direct initialization of the implementation contract.
2. **Add a `msg.sender` check** to the outer `initialize()` functions, restricting the caller to the deployer or the `Clearinghouse` proxy address (analogous to the TypeScript-based constant injection suggested in the reference report).

---

### Proof of Concept

1. Deployer broadcasts transaction A: deploy `SpotEngine` proxy (uninitialized).
2. Deployer broadcasts transaction B: call `Clearinghouse.addEngine(spotEngineProxy, offchainExchange, SPOT)`.
3. Attacker observes transaction B in the mempool and broadcasts transaction C with higher gas:
   ```solidity
   SpotEngine(spotEngineProxy).initialize(
       attackerClearinghouse,   // _clearinghouse
       attackerExchange,        // _offchainExchange
       anyAddress,              // _quote
       attackerEndpoint,        // _endpoint
       attacker                 // _admin → becomes owner
   );
   ```
4. Transaction C mines before B. `_initialize()` runs, sets `canApplyDeltas[attackerEndpoint/Clearinghouse/Exchange] = true`, transfers ownership to attacker.
5. Transaction B reverts: "Initializable: contract is already initialized."
6. Attacker calls `SpotEngine.updateBalance(QUOTE_PRODUCT_ID, victimSubaccount, -victimBalance)` from `attackerEndpoint`, draining the victim's quote balance to zero.

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

**File:** core/contracts/Clearinghouse.sol (L173-181)
```text
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
