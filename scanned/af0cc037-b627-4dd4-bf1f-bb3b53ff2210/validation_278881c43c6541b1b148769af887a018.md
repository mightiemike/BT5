### Title
Extension registered in `extensions` array but not wired to any hook order silently bypasses configured guard — (`metric-core/contracts/libraries/ValidateExtensionsConfig.sol`)

---

### Summary

`ValidateExtensionsConfig.validateExtensionsConfig()` does not verify that every registered extension is referenced in at least one hook order. An extension can be registered and initialized but never wired to any hook slot, causing the guard to silently fail open on all pool actions.

---

### Finding Description

`validateExtensionsConfig` enforces the following:

- No zero addresses in the `extensions` array
- No duplicate extension addresses
- At least one `ExtensionOrders` field is non-zero (if extensions are registered)
- Each order field only references valid indices (1 to `extensionCount`), no duplicates within a single order [1](#0-0) 

What it does **not** check is that every extension index (1 to `extensionCount`) appears in at least one of the six order fields. The following configuration passes validation without error:

```
extensions        = [depositAllowlist, swapAllowlist]   // 2 extensions registered
orders.beforeSwap = encode(2)                           // only swapAllowlist wired
orders.beforeAddLiquidity = 0                           // depositAllowlist NOT wired to any hook
```

`validateExtensionsConfig` accepts this because `_ordersAreZero(orders)` returns `false` (one order is non-zero) and `_validateExtensionOrder(orders.beforeSwap, 2)` passes (index 2 is within range). [2](#0-1) 

After deployment, the factory calls `initialize` on every extension in the array: [3](#0-2) 

So `depositAllowlist` is initialized and bound to the pool, but `_callExtensionsInOrder` is never invoked for it on any pool action because `BEFORE_ADD_LIQUIDITY_ORDER == 0`: [4](#0-3) 

The pool appears to have a deposit guard (the extension is initialized and bound) but `beforeAddLiquidity` is never dispatched to it. Any caller can `addLiquidity` without restriction.

The same gap applies to `SwapAllowlistExtension`, `OracleValueStopLossExtension`, and `PriceVelocityGuardExtension` — any of these can be registered but left unwired to the hook they are designed to protect. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool deployer who registers a `DepositAllowlistExtension` or `SwapAllowlistExtension` but omits the corresponding order field deploys a pool with silently broken access control. Unauthorized depositors can mint LP shares in a pool intended to be curated, and unauthorized swappers can trade against LP positions. For a `SwapAllowlistExtension` bypass this directly extracts value from LP principal. For a `DepositAllowlistExtension` bypass it allows unauthorized parties to earn fees and dilute curated LP positions. Both outcomes are direct fund-impacting consequences above contest thresholds.

---

### Likelihood Explanation

`createPool` is fully permissionless — any caller can supply the `extensions` array and `extensionOrders` struct. A pool creator who intends to gate deposits but forgets to populate `orders.beforeAddLiquidity` (or wires the extension only to `afterSwap`) will deploy a pool with no effective deposit guard. The validation gives no signal that the registered extension is inert. Because the factory initializes every extension regardless of wiring, the pool's on-chain state looks fully configured.

---

### Recommendation

Add a cross-check inside `validateExtensionsConfig` that every extension index from 1 to `extensionCount` appears in at least one order field:

```solidity
function validateExtensionsConfig(...) internal pure {
    // ... existing checks ...

    // NEW: every registered extension must be wired to at least one hook
    uint256 referenced;
    referenced |= _collectBits(orders.beforeAddLiquidity);
    referenced |= _collectBits(orders.afterAddLiquidity);
    referenced |= _collectBits(orders.beforeRemoveLiquidity);
    referenced |= _collectBits(orders.afterRemoveLiquidity);
    referenced |= _collectBits(orders.beforeSwap);
    referenced |= _collectBits(orders.afterSwap);

    for (uint256 i = 1; i <= extensions.length; i++) {
        if (referenced & (1 << i) == 0) revert InvalidExtensionsConfig();
    }
}

function _collectBits(uint256 order) private pure returns (uint256 bits) {
    while (order != 0) {
        bits |= (1 << (order & 0x7));
        order >>= 3;
    }
}
```

---

### Proof of Concept

```solidity
function test_extensionRegisteredButNotWiredToAnyHook() public {
    // Two extensions registered; only extension 2 (swapAllowlist) is wired.
    // Extension 1 (depositAllowlist) is initialized but never called.
    address[] memory extensions = new address[](2);
    extensions[0] = address(depositAllowlist);   // index 1
    extensions[1] = address(swapAllowlist);      // index 2
    bytes[] memory initData = new bytes[](2);

    ExtensionOrders memory orders;
    // Only beforeSwap references extension 2; beforeAddLiquidity is zero.
    orders.beforeSwap = ExtensionOrderTestLib.encodeExtensionOrder(2, 0, 0, 0, 0, 0, 0);

    // Passes validation — no revert despite depositAllowlist being unwired.
    harness.validateExtensionsConfig(extensions, orders, initData);

    // Deploy pool with this config; depositAllowlist.initialize() is called,
    // binding it to the pool, but beforeAddLiquidity order is 0.
    address pool = factory.createPool(buildParams(extensions, orders, initData));

    // Any address can now addLiquidity without restriction.
    vm.prank(unauthorizedDepositor);
    pool.addLiquidity(unauthorizedDepositor, 0, delta, "");
    // succeeds — DepositAllowlistExtension never ran
}
```

### Citations

**File:** metric-core/contracts/libraries/ValidateExtensionsConfig.sol (L14-42)
```text
  function validateExtensionsConfig(
    address[] calldata extensions,
    ExtensionOrders calldata orders,
    bytes[] calldata extensionInitData
  ) internal pure {
    if (extensions.length > MAX_EXTENSIONS) revert InvalidExtensionsConfig();
    if (extensionInitData.length != extensions.length) revert InvalidExtensionsConfig();

    if (extensions.length == 0) {
      if (!_ordersAreZero(orders)) revert InvalidExtensionsConfig();
      return;
    }

    for (uint256 i = 0; i < extensions.length; i++) {
      if (extensions[i] == address(0)) revert InvalidExtensionsConfig();
      for (uint256 j = 0; j < i; j++) {
        if (extensions[i] == extensions[j]) revert InvalidExtensionsConfig();
      }
    }

    if (_ordersAreZero(orders)) revert InvalidExtensionsConfig();

    _validateExtensionOrder(orders.beforeAddLiquidity, extensions.length);
    _validateExtensionOrder(orders.afterAddLiquidity, extensions.length);
    _validateExtensionOrder(orders.beforeRemoveLiquidity, extensions.length);
    _validateExtensionOrder(orders.afterRemoveLiquidity, extensions.length);
    _validateExtensionOrder(orders.beforeSwap, extensions.length);
    _validateExtensionOrder(orders.afterSwap, extensions.length);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L206-210)
```text
    for (uint256 i = 0; i < params.extensions.length; i++) {
      CallExtension.callExtension(
        params.extensions[i], abi.encodeCall(IMetricOmmExtensions.initialize, (pool, params.extensionInitData[i]))
      );
    }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
