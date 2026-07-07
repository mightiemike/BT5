### Title
Unprotected `initialize()` on `Endpoint` Implementation Contract Due to Missing `_disableInitializers()` — (`File: core/contracts/Endpoint.sol`)

---

### Summary

`Endpoint.sol` is deployed behind a transparent proxy (confirmed by its `_getProxyManager()` reading the EIP-1967 admin slot). It has an `initialize()` function guarded only by the `initializer` modifier, but its implementation contract has **no constructor calling `_disableInitializers()`**. This leaves the implementation's `initialize()` callable by any unprivileged actor, allowing them to seize ownership of the implementation contract and configure all critical protocol addresses on it.

---

### Finding Description

`Endpoint` inherits from `OwnableUpgradeable` and `EIP712Upgradeable` and exposes a public `initialize()` function marked `initializer`. However, unlike every other upgradeable contract in the codebase (`Airdrop`, `BaseProxyManager`, `BaseWithdrawPool`, `ContractOwner`, `Verifier` — all of which have a `constructor() { _disableInitializers(); }`), `Endpoint` has **no constructor at all**. [1](#0-0) 

The `initializer` modifier from OpenZeppelin only prevents the function from being called more than once on a given contract instance. Without `_disableInitializers()` in the constructor, the implementation contract itself starts in an uninitialized state and its `initialize()` remains open to any caller.

Compare with the correctly protected contracts: [2](#0-1) [3](#0-2) 

`Endpoint.initialize()` sets `sequencer`, `clearinghouse`, `offchainExchange`, `verifier`, `endpointTx`, `spotEngine`, and `perpEngine` — every critical protocol address — and calls `__Ownable_init()`, transferring ownership to the caller: [4](#0-3) 

The same pattern is present in `Clearinghouse.sol` (no constructor, `initialize()` with `initializer` modifier) and in `SpotEngine`/`PerpEngine` via `BaseEngine._initialize()`: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

An attacker who initializes the `Endpoint` implementation contract:

1. Becomes the `owner` of the implementation via `__Ownable_init()`.
2. Sets all critical addresses (`sequencer`, `clearinghouse`, `verifier`, `endpointTx`, etc.) to attacker-controlled contracts on the implementation instance.
3. The `endpointTx` address is used in every `_delegatecallEndpointTx()` call. If any contract in the system ever calls the implementation directly (rather than through the proxy), the attacker-controlled `endpointTx` would be `delegatecall`ed, executing arbitrary code in the caller's storage context. [7](#0-6) 

Additionally, in upgrade scenarios where the new implementation reads or inherits state from the old implementation contract, the attacker-controlled state (owner, sequencer, verifier, etc.) would be present and could corrupt the upgrade path or be used to pass `onlyOwner` checks on the implementation directly.

---

### Likelihood Explanation

**High.** The implementation address is publicly readable from the proxy's EIP-1967 implementation slot (`0x360894...`). No special permissions, private keys, or governance capture are required. Any EOA can call `initialize()` on the implementation in a single transaction. The `initializer` modifier provides no protection against a first-time call.

---

### Recommendation

Add a constructor to `Endpoint.sol` (and similarly to `Clearinghouse.sol`, `SpotEngine.sol`, `PerpEngine.sol`, `OffchainExchange.sol`, `ClearinghouseLiq.sol`) that calls `_disableInitializers()`, matching the pattern already used in the rest of the codebase:

```solidity
/// @custom:oz-upgrades-unsafe-allow constructor
constructor() {
    _disableInitializers();
}
``` [8](#0-7) 

---

### Proof of Concept

```solidity
// Attacker reads implementation address from proxy EIP-1967 slot
address impl = address(uint160(uint256(
    vm.load(endpointProxy, 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc)
)));

// Attacker calls initialize() directly on the implementation
Endpoint(impl).initialize(
    attackerAddress,   // _sanctions
    attackerAddress,   // _sequencer
    attackerAddress,   // _offchainExchange
    IClearinghouse(attackerAddress), // _clearinghouse
    attackerAddress,   // _verifier
    attackerAddress    // _endpointTx  ← malicious delegatecall target
);

// Attacker now owns the implementation and controls all its critical addresses.
// Any direct call to the implementation that triggers _delegatecallEndpointTx()
// will delegatecall into attacker-controlled code.
``` [9](#0-8)

### Citations

**File:** core/contracts/Endpoint.sol (L23-28)
```text
contract Endpoint is
    EIP712Upgradeable,
    OwnableUpgradeable,
    EndpointStorage,
    IEndpoint
{
```

**File:** core/contracts/Endpoint.sol (L31-66)
```text
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

**File:** core/contracts/Endpoint.sol (L68-84)
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
            }
        }
        return result;
    }
```

**File:** core/contracts/Endpoint.sol (L359-366)
```text
    function _getProxyManager() internal view returns (address) {
        AddressSlot storage proxyAdmin;
        // solhint-disable-next-line no-inline-assembly
        assembly {
            proxyAdmin.slot := 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103
        }
        return proxyAdmin.value;
    }
```

**File:** core/contracts/Airdrop.sol (L19-22)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/BaseProxyManager.sol (L102-105)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/Clearinghouse.sol (L25-40)
```text
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
