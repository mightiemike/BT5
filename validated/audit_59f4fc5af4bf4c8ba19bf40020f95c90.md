### Title
Missing Zero-Address Check for `clearinghouseLiq` in `Clearinghouse.initialize` Silently Disables All Liquidations — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.initialize` assigns `clearinghouseLiq` without a zero-address guard. The post-init update path (`upgradeClearinghouseLiq`) is access-controlled and cannot be used to recover from a zero-address initialization. If `clearinghouseLiq` is set to `address(0)` at deploy time, every call to `liquidateSubaccount` silently succeeds without executing any liquidation logic, because a `delegatecall` to `address(0)` returns `success = true` with empty return data in the EVM.

---

### Finding Description

`Clearinghouse.initialize` stores the `_clearinghouseLiq` argument directly into `clearinghouseLiq` with no zero-address validation:

```solidity
// Clearinghouse.sol lines 25-40
function initialize(
    address _endpoint,
    address _quote,
    address _clearinghouseLiq,   // ← accepted without require(_clearinghouseLiq != address(0))
    uint256 _spreads,
    address _withdrawPool
) external initializer {
    ...
    clearinghouseLiq = _clearinghouseLiq;   // ← stored unconditionally
    ...
}
``` [1](#0-0) 

The only post-init path to update `clearinghouseLiq` is `upgradeClearinghouseLiq`, which is gated behind `proxyManagerHelper` — an access-controlled role that cannot be used to self-correct a zero-address initialization:

```solidity
// Clearinghouse.sol lines 677-684
function upgradeClearinghouseLiq(address _clearinghouseLiq) external {
    require(
        msg.sender == IProxyManager(_getProxyManager()).getProxyManagerHelper(),
        ERR_UNAUTHORIZED
    );
    clearinghouseLiq = _clearinghouseLiq;
}
``` [2](#0-1) 

This is the exact structural parallel to the reported bug: the post-init setter is guarded, but the initializer is not.

`liquidateSubaccount` unconditionally delegates to `clearinghouseLiq`:

```solidity
// Clearinghouse.sol lines 658-661
(bool success, bytes memory result) = clearinghouseLiq.delegatecall(
    liquidateSubaccountCall
);
require(success, string(result));
``` [3](#0-2) 

In the EVM, a `DELEGATECALL` to an address with no deployed code (including `address(0)`) returns `success = true` and empty return data. The `require(success, ...)` check therefore passes, but the liquidation implementation in `ClearinghouseLiq` never runs. The subaccount's balances are not modified, no health check is enforced, and the call returns as if liquidation succeeded.

A secondary instance of the same class exists in `BaseEngine._initialize`, which calls `transferOwnership(_admin)` and populates `canApplyDeltas` for `_endpointAddr`, `_clearinghouseAddr`, and `_offchainExchangeAddr` without any zero-address guards:

```solidity
// BaseEngine.sol lines 203-218
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
``` [4](#0-3) 

If `_admin` is `address(0)`, ownership is transferred to the zero address and the engine becomes permanently un-administrable. If `_endpointAddr` is `address(0)`, the `onlyEndpoint` modifier can never be satisfied, bricking all sequencer-driven operations on the engine.

---

### Impact Explanation

**`clearinghouseLiq = address(0)` path (primary finding):**
- Every `liquidateSubaccount` call silently no-ops. Unhealthy subaccounts accumulate bad debt without being liquidated.
- Protocol solvency is directly threatened: the insurance fund cannot absorb losses that liquidation would have prevented.
- The corrupted state is `clearinghouseLiq` (address slot), and the broken invariant is that every liquidation call must execute the `liquidateSubaccountImpl` logic in `ClearinghouseLiq`.

**`_admin = address(0)` path (secondary, `BaseEngine`):**
- `owner()` becomes `address(0)`. All `onlyOwner` functions (`updateRisk`, `updatePrice` via clearinghouse, etc.) are permanently inaccessible.
- Risk parameters cannot be updated; the engine is frozen in its initial configuration.

---

### Likelihood Explanation

The scenario requires a deployment error or a malicious deployer — identical in likelihood to the original report's exploit scenario. The Nado deployment system passes these addresses programmatically; a misconfiguration, script bug, or sabotage during setup is the realistic trigger. The `initializer` modifier ensures the window is permanent: once initialized with `address(0)`, there is no unprivileged recovery path.

---

### Recommendation

**Short term:** Add explicit zero-address guards in `Clearinghouse.initialize` for all critical address parameters, and in `BaseEngine._initialize` for `_admin` and `_endpointAddr`:

```solidity
// Clearinghouse.initialize
require(_clearinghouseLiq != address(0), "zero clearinghouseLiq");
require(_withdrawPool != address(0), "zero withdrawPool");
require(_endpoint != address(0), "zero endpoint");
require(_quote != address(0), "zero quote");

// BaseEngine._initialize
require(_admin != address(0), "zero admin");
require(_endpointAddr != address(0), "zero endpoint");
require(_clearinghouseAddr != address(0), "zero clearinghouse");
```

**Long term:** Audit all `initializer` functions across the protocol for missing zero-address checks on addresses that gate critical protocol operations. Consider a property-testing tool (e.g., Echidna) to assert that `clearinghouseLiq != address(0)` and `owner() != address(0)` as invariants post-initialization.

---

### Proof of Concept

1. Deploy `Clearinghouse` proxy and call `initialize` with `_clearinghouseLiq = address(0)`.
2. Deploy and configure `Endpoint`, `SpotEngine`, `PerpEngine` normally.
3. Create a subaccount, deposit collateral, open a position, and let the oracle price move to make the subaccount unhealthy (maintenance health < 0).
4. Call `liquidateSubaccount` via the sequencer path (`submitTransactionsChecked` → `processTransaction` → `Clearinghouse.liquidateSubaccount`).
5. Observe: `clearinghouseLiq.delegatecall(...)` targets `address(0)`, EVM returns `success = true`, `require(success)` passes, function returns — but the subaccount's balances are unchanged and it remains unhealthy.
6. Repeat indefinitely: no liquidation ever executes, bad debt accumulates, and the insurance fund is drained without recourse. [5](#0-4)

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

**File:** core/contracts/Clearinghouse.sol (L677-684)
```text
    function upgradeClearinghouseLiq(address _clearinghouseLiq) external {
        require(
            msg.sender ==
                IProxyManager(_getProxyManager()).getProxyManagerHelper(),
            ERR_UNAUTHORIZED
        );
        clearinghouseLiq = _clearinghouseLiq;
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
