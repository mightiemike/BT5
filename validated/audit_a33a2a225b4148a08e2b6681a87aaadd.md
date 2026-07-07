### Title
Missing `initializer` Modifier on Public `initialize()` Functions Allows Front-Running Ownership Takeover — (`WithdrawPool.sol`, `SpotEngine.sol`, `PerpEngine.sol`)

---

### Summary

`WithdrawPool.initialize()`, `SpotEngine.initialize()`, and `PerpEngine.initialize()` are all declared `external` with **no `initializer` modifier and no access control**. They delegate initialization protection to an internal `_initialize()` function that carries the `initializer` modifier. Because the public entry point is unguarded, any unprivileged caller can race the deployer to call `initialize()` first, claim ownership, and supply attacker-controlled constructor parameters — including the `clearinghouse` and `verifier` addresses for `WithdrawPool`, and the `canApplyDeltas` whitelist for the engines.

---

### Finding Description

`WithdrawPool.initialize()` is declared as a plain `external` function with no modifier: [1](#0-0) 

It delegates to `BaseWithdrawPool._initialize()`, which carries the `initializer` modifier: [2](#0-1) 

`__Ownable_init()` inside `_initialize()` sets `msg.sender` as owner. Because the public `initialize()` has no `initializer` modifier and no access control, the **first external caller** — not necessarily the deployer — wins ownership.

The same pattern exists in `SpotEngine.initialize()`: [3](#0-2) 

And `PerpEngine.initialize()`: [4](#0-3) 

Both delegate to `BaseEngine._initialize()`, which carries `initializer` and calls `__Ownable_init()` followed by `transferOwnership(_admin)`: [5](#0-4) 

By contrast, every other upgradeable contract in the codebase — `Clearinghouse`, `Endpoint`, `OffchainExchange`, `Verifier`, `Airdrop`, `ContractOwner`, `BaseProxyManager` — places `initializer` directly on the public `initialize()` function: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**`WithdrawPool` (highest impact):** An attacker who front-runs `WithdrawPool.initialize()` with the legitimate `_clearinghouse` and `_verifier` addresses (copied from the pending deployer transaction) becomes the contract owner. Once the pool accumulates withdrawal liquidity, the attacker calls `removeLiquidity()` — an `onlyOwner` function — to drain all token balances held by the pool: [8](#0-7) 

**`SpotEngine` / `PerpEngine` (secondary impact):** An attacker who front-runs engine initialization becomes owner and controls the `canApplyDeltas` whitelist. They can subsequently call `updateRisk()` to set extreme weight parameters (e.g., zero maintenance weights), corrupting health calculations for all subaccounts and enabling undercollateralized positions or blocking legitimate liquidations: [9](#0-8) 

---

### Likelihood Explanation

The attack window is the gap between proxy deployment and the deployer's `initialize()` call — a standard mempool front-running opportunity on any public EVM chain. The attacker needs only to observe the pending deployment transaction, copy the intended parameters, and submit with higher gas. No privileged access, leaked keys, or social engineering is required. The deployer's subsequent `initialize()` call reverts silently (because `_initialized` is already set), and if the deployer's script does not assert post-initialization ownership, the attacker-controlled state persists undetected.

---

### Recommendation

Add the `initializer` modifier directly to each public `initialize()` function, consistent with every other upgradeable contract in the codebase:

```solidity
// WithdrawPool.sol
function initialize(address _clearinghouse, address _verifier)
    external
    initializer   // ← add this
{
    _initialize(_clearinghouse, _verifier);
}

// SpotEngine.sol
function initialize(...) external initializer { ... }

// PerpEngine.sol
function initialize(...) external initializer { ... }
```

Placing `initializer` on the public function ensures the OZ `Initializable` guard fires at the outermost call frame, preventing any caller from racing the deployer.

---

### Proof of Concept

1. Deployer broadcasts a transaction to call `WithdrawPool.initialize(clearinghouse, verifier)`.
2. Attacker observes the pending transaction in the mempool, extracts `clearinghouse` and `verifier`, and submits `WithdrawPool.initialize(clearinghouse, verifier)` with higher gas.
3. Attacker's transaction is mined first. `BaseWithdrawPool._initialize()` runs: `__Ownable_init()` sets attacker as owner; `clearinghouse` and `verifier` are set to legitimate values (so the pool appears functional).
4. Deployer's transaction is mined next. `_initialize()` reverts because `_initialized == 1`. If the deployment script does not assert success or check `owner()`, the failure is silently swallowed.
5. `Clearinghouse.setWithdrawPool(withdrawPool)` is called by the owner; the pool address is registered normally.
6. Users deposit collateral; withdrawal funds accumulate in `WithdrawPool`.
7. Attacker calls `WithdrawPool.removeLiquidity(productId, balance, attacker)` for each token, draining the pool. [1](#0-0) [2](#0-1) [8](#0-7)

### Citations

**File:** core/contracts/WithdrawPool.sol (L16-18)
```text
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L23-30)
```text
    function _initialize(address _clearinghouse, address _verifier)
        internal
        initializer
    {
        __Ownable_init();
        clearinghouse = _clearinghouse;
        verifier = _verifier;
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```

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

**File:** core/contracts/BaseEngine.sol (L278-290)
```text
    function updateRisk(uint32 productId, RiskHelper.RiskStore memory riskStore)
        external
        onlyOwner
    {
        require(
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance,
            ERR_BAD_PRODUCT_CONFIG
        );

        _risk().value[productId] = riskStore;
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
