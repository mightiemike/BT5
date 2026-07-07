### Title
Upgradeable Contracts Missing `_disableInitializers()` in Constructor Allow Attacker to Seize Implementation Contract Ownership — (`File: core/contracts/Endpoint.sol`, `core/contracts/Clearinghouse.sol`, `core/contracts/OffchainExchange.sol`)

---

### Summary

Several core Nado upgradeable contracts inherit from OpenZeppelin upgradeable base contracts and expose an `initialize()` function protected only by the `initializer` modifier, but define **no constructor** that calls `_disableInitializers()`. This leaves the bare implementation contract uninitialized and claimable by any external caller before the deployer acts, matching the exact vulnerability class described in the reference report.

---

### Finding Description

The following production contracts are upgradeable (inherit `OwnableUpgradeable`, `EIP712Upgradeable`, or `EndpointGated` which itself inherits `OwnableUpgradeable`) and expose a public `initialize()` function with the `initializer` modifier, but contain **no constructor** calling `_disableInitializers()`:

**`Endpoint.sol`** [1](#0-0) 

The contract inherits `EIP712Upgradeable` and `OwnableUpgradeable` and exposes `initialize()` with `initializer`, but has no constructor at all — no `_disableInitializers()` call is ever made on the implementation. [2](#0-1) 

**`Clearinghouse.sol`** [3](#0-2) 

Inherits `EndpointGated` → `OwnableUpgradeable`, exposes `initialize()` with `initializer`, no constructor. [4](#0-3) 

**`OffchainExchange.sol`** [5](#0-4) 

Imports `Initializable`, inherits `EIP712Upgradeable` and `EndpointGated`, exposes `initialize()` with `initializer`, no constructor. [6](#0-5) 

By contrast, the contracts that were correctly fixed — `Airdrop`, `BaseProxyManager`, `ContractOwner`, `Verifier`, and `BaseWithdrawPool` — all use the recommended pattern:

```solidity
/// @custom:oz-upgrades-unsafe-allow constructor
constructor() {
    _disableInitializers();
}
``` [7](#0-6) [8](#0-7) 

The three affected contracts have no such protection.

---

### Impact Explanation

When the proxy and implementation are deployed, `initialize()` is called in the context of the proxy. The implementation contract itself remains uninitialized. An attacker who calls `initialize()` directly on the `Endpoint`, `Clearinghouse`, or `OffchainExchange` implementation contract becomes the `owner` of that implementation. As owner, the attacker can:

- Call any `onlyOwner`-gated functions directly on the implementation, corrupting the implementation contract's own storage.
- For `Endpoint` specifically: set `sequencer`, `offchainExchange`, `verifier`, `endpointTx`, and `sanctions` to attacker-controlled addresses in the implementation's storage slot layout.
- For `Endpoint` specifically: `_delegatecallEndpointTx` performs a `delegatecall` to `endpointTx`; if an attacker controls the implementation and sets `endpointTx` to a malicious contract, any direct call to the implementation (not through the proxy) executes arbitrary code in the implementation's execution context. [9](#0-8) 

While the proxy's own storage is not directly corrupted (state lives in the proxy's slots), the implementation contract is permanently under attacker control. This breaks the integrity assumption of the upgrade system managed by `BaseProxyManager`, which tracks `codeHashes` and `pendingImpls` but cannot prevent an attacker-owned implementation from being manipulated directly. [10](#0-9) 

---

### Likelihood Explanation

The attack requires no special privilege. Any unprivileged external caller can call `initialize()` on the bare implementation address at any time before the deployer does so. The deployer must make a separate transaction to initialize the implementation (or rely on the proxy initialization not covering the implementation), creating a front-running window. This is a well-known, actively exploited attack class with realistic on-chain likelihood.

---

### Recommendation

Add the following constructor to each affected contract, exactly as already done in `Verifier.sol`, `BaseWithdrawPool.sol`, `Airdrop.sol`, `ContractOwner.sol`, and `BaseProxyManager.sol`:

```solidity
/// @custom:oz-upgrades-unsafe-allow constructor
constructor() {
    _disableInitializers();
}
```

This must be added to:
- `core/contracts/Endpoint.sol`
- `core/contracts/Clearinghouse.sol`
- `core/contracts/OffchainExchange.sol`

Additionally audit `EndpointTx.sol` and `ClearinghouseLiq.sol` for the same gap, as both inherit upgradeable base contracts and have no constructor. [11](#0-10) [12](#0-11) 

---

### Proof of Concept

```
1. ProxyManager deploys Endpoint implementation at address IMPL.
2. ProxyManager deploys TransparentUpgradeableProxy pointing to IMPL.
3. ProxyManager calls proxy.initialize(...) — this initializes the proxy's storage.
   IMPL itself remains uninitialized (initializer flag not set on IMPL).
4. Attacker observes IMPL address on-chain (or from deployment tx).
5. Attacker calls IMPL.initialize(
       attacker_sanctions,
       attacker_sequencer,
       attacker_offchainExchange,
       attacker_clearinghouse,
       attacker_verifier,
       attacker_endpointTx
   )
   → succeeds because IMPL's `_initialized` flag is 0.
   → Attacker is now owner of IMPL.
6. Attacker calls IMPL.onlyOwner_function() directly, corrupting IMPL's storage.
7. Attacker sets endpointTx on IMPL to a malicious contract.
8. Any direct call to IMPL (not through proxy) now executes attacker-controlled
   delegatecall logic via _delegatecallEndpointTx.
``` [2](#0-1) [9](#0-8)

### Citations

**File:** core/contracts/Endpoint.sol (L23-66)
```text
contract Endpoint is
    EIP712Upgradeable,
    OwnableUpgradeable,
    EndpointStorage,
    IEndpoint
{
    using ERC20Helper for IERC20Base;

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

**File:** core/contracts/Endpoint.sol (L68-80)
```text
    function _delegatecallEndpointTx(bytes memory callData)
        internal
        returns (bytes memory)
    {
        require(endpointTx != address(0), "Endpoint Tx not set");
        (bool success, bytes memory result) = endpointTx.delegatecall(callData);
        if (!success) {
            if (result.length == 0) {
                revert();
            }
            // solhint-disable-next-line no-inline-assembly
            assembly {
                revert(add(result, 0x20), mload(result))
```

**File:** core/contracts/Clearinghouse.sol (L21-40)
```text
contract Clearinghouse is EndpointGated, ClearinghouseStorage, IClearinghouse {
    using MathSD21x18 for int128;
    using ERC20Helper for IERC20Base;

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

**File:** core/contracts/OffchainExchange.sol (L20-24)
```text
contract OffchainExchange is
    IOffchainExchange,
    EndpointGated,
    EIP712Upgradeable
{
```

**File:** core/contracts/OffchainExchange.sol (L243-258)
```text
    function initialize(address _clearinghouse, address _endpoint)
        external
        initializer
    {
        __Ownable_init();
        setEndpoint(_endpoint);

        __EIP712_init("Nado", "0.0.1");
        clearinghouse = IClearinghouse(_clearinghouse);
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
    }
```

**File:** core/contracts/Verifier.sol (L36-39)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L18-21)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/BaseProxyManager.sol (L84-88)
```text
    mapping(string => address) public proxies;
    mapping(string => address) public pendingImpls;
    mapping(string => bytes32) public pendingHashes;
    mapping(string => bytes32) public codeHashes;

```

**File:** core/contracts/EndpointTx.sol (L14-15)
```text
contract EndpointTx is EIP712Upgradeable, OwnableUpgradeable, EndpointStorage {
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ClearinghouseLiq.sol (L19-23)
```text
contract ClearinghouseLiq is
    EndpointGated,
    ClearinghouseStorage,
    IClearinghouseLiq
{
```
