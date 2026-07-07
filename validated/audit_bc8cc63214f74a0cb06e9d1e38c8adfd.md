### Title
Uninitialized Clearinghouse Implementation Allows Arbitrary `delegatecall` and Permanent Protocol Destruction — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.sol`, `Endpoint.sol`, `SpotEngine.sol`, and `PerpEngine.sol` are deployed behind ERC1967 upgradeable proxies but their implementation contracts do not call `_disableInitializers()` in a constructor. This leaves the implementation contracts permanently open to direct `initialize()` calls by any unprivileged attacker. For `Clearinghouse`, the consequence is catastrophic: the attacker can become owner and endpoint, then exploit the existing `clearinghouseLiq.delegatecall(...)` path to `selfdestruct` the implementation, permanently bricking every proxy that points to it and locking all user funds.

---

### Finding Description

OpenZeppelin's upgradeable contract pattern requires that implementation contracts call `_disableInitializers()` in their constructor to prevent anyone from calling `initialize()` directly on the implementation (as opposed to through the proxy). Without this guard, the implementation's `initializer` modifier does not block the call because the implementation's own `_initialized` flag is never set.

The grep result confirms that `_disableInitializers()` is present only in `BaseProxyManager.sol`, `ContractOwner.sol`, `Verifier.sol`, `Airdrop.sol`, and `BaseWithdrawPool.sol`. It is **absent** from:

- `Clearinghouse.sol`
- `Endpoint.sol`
- `SpotEngine.sol`
- `PerpEngine.sol`
- `OffchainExchange.sol`

The most severe case is `Clearinghouse.sol`. Its `initialize()` function is `external initializer` with no constructor guard: [1](#0-0) 

This function accepts attacker-controlled values for `_endpoint` and `_clearinghouseLiq`. Once called on the implementation directly, the attacker controls both the `endpoint` address (checked by `onlyEndpoint`) and the `clearinghouseLiq` address (used as the `delegatecall` target).

`Clearinghouse.liquidateSubaccount()` then performs an unconstrained `delegatecall` to `clearinghouseLiq`: [2](#0-1) 

Because the attacker set `_endpoint` to their own address during `initialize()`, the `onlyEndpoint` modifier passes. The attacker-controlled `clearinghouseLiq` can contain a `selfdestruct` opcode, which executes in the context of the `Clearinghouse` implementation and destroys it.

The same structural issue exists in `Endpoint.sol`, which has an `initialize()` accepting `_endpointTx` and `_verifier`, and performs `endpointTx.delegatecall(callData)` via `_delegatecallEndpointTx()`: [3](#0-2) [4](#0-3) 

An attacker who initializes the `Endpoint` implementation with themselves as `sequencer` and a malicious `verifier` + `endpointTx` can call `submitTransactionsChecked()` to trigger `_delegatecallEndpointTx()` into a `selfdestruct`.

---

### Impact Explanation

Destroying the `Clearinghouse` implementation makes every proxy pointing to it permanently non-functional. All user subaccount balances, collateral, and positions become inaccessible. There is no upgrade path because the proxy's admin upgrade mechanism itself calls through the now-dead implementation. All deposited assets — spot collateral, insurance fund, and NLP pool balances — are permanently locked with no recovery path. This is a total loss of all protocol funds.

---

### Likelihood Explanation

The attack requires no special privileges, no leaked keys, and no governance capture. Any EOA can call `initialize()` on the deployed implementation address at any time after deployment. The implementation address is publicly readable from the ERC1967 implementation slot. The attack is a single atomic transaction sequence and is irreversible.

---

### Recommendation

Add a constructor with `_disableInitializers()` to every upgradeable implementation contract that is currently missing it, following the same pattern already used in `Verifier.sol`: [5](#0-4) 

Apply this to `Clearinghouse.sol`, `Endpoint.sol`, `SpotEngine.sol`, `PerpEngine.sol`, and `OffchainExchange.sol`:

```solidity
/// @custom:oz-upgrades-unsafe-allow constructor
constructor() {
    _disableInitializers();
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

// Malicious ClearinghouseLiq replacement
contract MaliciousClearinghouseLiq {
    function liquidateSubaccountImpl(bytes calldata) external {
        selfdestruct(payable(msg.sender));
    }
}
```

Attack sequence:

1. Read the `Clearinghouse` implementation address from the ERC1967 slot of the deployed proxy.
2. Call `Clearinghouse(impl).initialize(attacker, anyQuote, address(maliciousClearinghouseLiq), 0, anyWithdrawPool)` directly on the implementation. This succeeds because `_initialized` is `0` on the implementation.
3. Call `Clearinghouse(impl).liquidateSubaccount(txn)` from `attacker` (who is now `endpoint`). The `onlyEndpoint` modifier passes. `clearinghouseLiq.delegatecall(...)` executes `selfdestruct` in the implementation's context.
4. `Clearinghouse` implementation code size drops to `0`. Every proxy that points to it now reverts on all calls. All user funds are permanently locked.

### Citations

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

**File:** core/contracts/Verifier.sol (L36-39)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```
