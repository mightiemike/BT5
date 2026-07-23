### Title
`ValidateExtensionsConfig` Does Not Verify Hook Implementation, Allowing Any Single-Hook Extension Registered as `beforeRemoveLiquidity` to Permanently Lock LP Principal — (`metric-core/contracts/libraries/ValidateExtensionsConfig.sol`)

---

### Summary

`ValidateExtensionsConfig.validateExtensionsConfig()` validates that extension order indices are in-range and non-duplicate, but never verifies that the referenced extension contract actually implements the hook it is registered for. When a production extension such as `DepositAllowlistExtension` — which only overrides `beforeAddLiquidity` — is also registered as the `beforeRemoveLiquidity` handler, every subsequent `removeLiquidity` call reverts unconditionally via `BaseMetricExtension.beforeRemoveLiquidity()` → `revert ExtensionNotImplemented()`. Extension addresses and orders are immutable after pool deployment; there is no admin escape path. All LP principal deposited into the pool is permanently locked.

---

### Finding Description

**Root cause — `ValidateExtensionsConfig._validateExtensionOrder`** [1](#0-0) 

The validation loop only checks that each 3-bit slot in the packed order word contains an index in `[1, extensionCount]` and that no index appears twice. It performs no call to the extension contract and has no knowledge of which `IMetricOmmExtensions` selectors the extension actually overrides.

**Default revert in `BaseMetricExtension`** [2](#0-1) 

Every hook that a concrete extension does not override unconditionally reverts with `ExtensionNotImplemented()`. There is no no-op fallback.

**`DepositAllowlistExtension` only overrides `beforeAddLiquidity`** [3](#0-2) 

`beforeRemoveLiquidity`, `afterRemoveLiquidity`, `beforeSwap`, and `afterSwap` are all inherited from `BaseMetricExtension` and all revert.

**Extension orders are immutable after deployment** [4](#0-3) 

All six order words are stored as `immutable` values in the constructor. There is no setter; the factory provides no upgrade or override path.

**`removeLiquidity` calls `_beforeRemoveLiquidity` unconditionally** [5](#0-4) 

If `BEFORE_REMOVE_LIQUIDITY_ORDER` is non-zero, `_callExtensionsInOrder` calls the registered extension. The revert from `ExtensionNotImplemented()` propagates out of `CallExtension.callExtension` and aborts `removeLiquidity`.

**Concrete misconfiguration path**

A pool admin calls `factory.createPool(params)` with:
- `params.extensions = [address(depositAllowlistExtension)]`
- `params.extensionOrders.beforeAddLiquidity = 1` (extension 1 → `DepositAllowlistExtension`)
- `params.extensionOrders.beforeRemoveLiquidity = 1` (extension 1 → `DepositAllowlistExtension`)

`ValidateExtensionsConfig.validateExtensionsConfig` accepts this configuration because index `1` is in `[1, 1]` and appears only once in each order word. [6](#0-5) 

After deployment, every `removeLiquidity` call hits `DepositAllowlistExtension.beforeRemoveLiquidity` → `BaseMetricExtension.beforeRemoveLiquidity` → `revert ExtensionNotImplemented()`. The pool is permanently bricked for withdrawals.

The same misconfiguration is possible with `PriceVelocityGuardExtension` (only overrides `beforeSwap`) and `OracleValueStopLossExtension` (only overrides `afterSwap`) registered for any liquidity hook.

---

### Impact Explanation

All LP shares deposited into the misconfigured pool are permanently unrecoverable. `removeLiquidity` reverts on every call. There is no admin function to change extension orders post-deployment, no factory rescue path, and no pause-bypass for liquidity withdrawal. The pool's token balances are stranded in the contract indefinitely. This is a direct, permanent loss of user principal.

---

### Likelihood Explanation

The trigger is a pool admin providing an `ExtensionOrders` struct that routes `beforeRemoveLiquidity` to an extension that does not implement it. This is an easy mistake: the factory's validation gives no feedback about unimplemented hooks, and the `ExtensionOrders` struct has six independent fields that must each be set correctly. A pool admin integrating `DepositAllowlistExtension` for the first time may copy an order template that includes a `beforeRemoveLiquidity` slot. The misconfiguration is silent at creation time and only manifests when the first LP attempts to withdraw. Likelihood is medium.

---

### Recommendation

In `ValidateExtensionsConfig.validateExtensionsConfig`, for each non-zero order, perform a static call to the extension contract for the corresponding selector and verify that it returns the expected selector (not `ExtensionNotImplemented`). Alternatively, require each extension to declare a bitmask of implemented hooks during `initialize`, and cross-check that bitmask against the registered orders at pool creation time.

As a defence-in-depth measure, `BaseMetricExtension` could return the correct selector (a no-op pass-through) instead of reverting for unimplemented hooks, making unregistered hooks safe to call.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Demonstrates that ValidateExtensionsConfig accepts a DepositAllowlistExtension
// registered for beforeRemoveLiquidity, and that removeLiquidity then reverts.

// 1. Deploy factory + deployer + DepositAllowlistExtension(factory).
// 2. Create pool with:
//      extensions = [address(depositAllowlistExt)]
//      extensionOrders.beforeAddLiquidity  = 1   // valid: ext implements this
//      extensionOrders.beforeRemoveLiquidity = 1 // INVALID: ext does NOT implement this
//    → createPool succeeds (ValidateExtensionsConfig does not revert).
// 3. LP calls addLiquidity → succeeds (beforeAddLiquidity passes).
// 4. LP calls removeLiquidity →
//      _beforeRemoveLiquidity calls DepositAllowlistExtension.beforeRemoveLiquidity
//      → BaseMetricExtension.beforeRemoveLiquidity → revert ExtensionNotImplemented()
//    → LP principal permanently locked.
// 5. No admin function exists to change BEFORE_REMOVE_LIQUIDITY_ORDER (immutable).
```

The `test_gateExtension_removeLiquidityIgnoresDepositGate` test in `metric-core/test/MetricOmmPool.extensions.t.sol` demonstrates the correct case (deposit gate only on `beforeAddLiquidity`), but no test covers the misconfigured case where the same extension is also wired to `beforeRemoveLiquidity`. [7](#0-6)

### Citations

**File:** metric-core/contracts/libraries/ValidateExtensionsConfig.sol (L36-41)
```text
    _validateExtensionOrder(orders.beforeAddLiquidity, extensions.length);
    _validateExtensionOrder(orders.afterAddLiquidity, extensions.length);
    _validateExtensionOrder(orders.beforeRemoveLiquidity, extensions.length);
    _validateExtensionOrder(orders.afterRemoveLiquidity, extensions.length);
    _validateExtensionOrder(orders.beforeSwap, extensions.length);
    _validateExtensionOrder(orders.afterSwap, extensions.length);
```

**File:** metric-core/contracts/libraries/ValidateExtensionsConfig.sol (L50-68)
```text
  function _validateExtensionOrder(uint256 order, uint256 extensionCount) private pure {
    unchecked {
      if (order == 0) return;

      uint256 seen;

      // this loop can run at most 7 times
      while (order != 0) {
        uint256 extensionIndex = order & 0x7;
        if (extensionIndex == 0 || extensionIndex > extensionCount) {
          revert InvalidExtensionOrder();
        }
        // forge-lint: disable-next-line(incorrect-shift) -- `1 << extensionIndex` sets bit `extensionIndex`; operands are correct.
        if (seen & (1 << extensionIndex) != 0) revert InvalidExtensionOrder();
        // forge-lint: disable-next-line(incorrect-shift) -- `1 << extensionIndex` sets bit `extensionIndex`; operands are correct.
        seen |= (1 << extensionIndex);
        order >>= 3;
      }
    }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L63-70)
```text
  function beforeRemoveLiquidity(address, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L37-51)
```text
  constructor(PoolExtensions memory extensions, ExtensionOrders memory extensionOrders) {
    EXTENSION_1 = extensions.extension1;
    EXTENSION_2 = extensions.extension2;
    EXTENSION_3 = extensions.extension3;
    EXTENSION_4 = extensions.extension4;
    EXTENSION_5 = extensions.extension5;
    EXTENSION_6 = extensions.extension6;
    EXTENSION_7 = extensions.extension7;
    BEFORE_ADD_LIQUIDITY_ORDER = extensionOrders.beforeAddLiquidity;
    AFTER_ADD_LIQUIDITY_ORDER = extensionOrders.afterAddLiquidity;
    BEFORE_REMOVE_LIQUIDITY_ORDER = extensionOrders.beforeRemoveLiquidity;
    AFTER_REMOVE_LIQUIDITY_ORDER = extensionOrders.afterRemoveLiquidity;
    BEFORE_SWAP_ORDER = extensionOrders.beforeSwap;
    AFTER_SWAP_ORDER = extensionOrders.afterSwap;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L199-211)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
```

**File:** metric-core/test/MetricOmmPool.extensions.t.sol (L213-224)
```text
  function test_gateExtension_removeLiquidityIgnoresDepositGate() public {
    GateExtension extension = new GateExtension();
    _deployPoolWithExtension(address(extension), _extensionOrdersWithBeforeAddLiquidity());
    extension.bindPool(address(pool));
    _approveUsersForPool(address(pool));

    extension.setAllowDeposit(true);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);

    extension.setAllowDeposit(false);
    _removeLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }
```
