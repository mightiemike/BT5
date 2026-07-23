### Title
Immutable Extension Slots Cannot Be Disabled When Broken, Permanently Locking LP Withdrawals and Swap Flows — (File: `metric-core/contracts/ExtensionCalling.sol`)

---

### Summary

All seven extension addresses and their six hook-order bitmaps are stored as `immutable` variables in `ExtensionCalling`. Once a pool is deployed, no function exists to disable, replace, or skip a broken extension. If any extension registered for `beforeRemoveLiquidity` or `afterRemoveLiquidity` permanently reverts — for example because an oracle feed it depends on becomes unavailable — LPs have no path to recover their principal. The pool admin's only lever is `pausePool`, but `removeLiquidity` carries no `whenNotPaused` guard, so pausing does not help. The factory owner likewise has no removal mechanism.

---

### Finding Description

`ExtensionCalling` stores all extension state as EVM immutables: [1](#0-0) 

These are written once in the constructor and are thereafter unalterable: [2](#0-1) 

Every pool action unconditionally calls through the configured order with no try/catch and no bypass flag: [3](#0-2) 

`MetricOmmPool.removeLiquidity` calls both `_beforeRemoveLiquidity` and `_afterRemoveLiquidity` but carries **no** `whenNotPaused` modifier: [4](#0-3) 

`addLiquidity` is identical — no pause guard, but extensions are still invoked: [5](#0-4) 

Only `swap` carries `whenNotPaused`: [6](#0-5) 

The factory exposes `pausePool` / `protocolPausePool` for the admin and protocol owner respectively, but neither path touches extension slots, and neither affects `removeLiquidity` reachability: [7](#0-6) 

There is no `disableExtension`, `replaceExtension`, or emergency-bypass function anywhere in the factory or pool.

---

### Impact Explanation

If an extension registered for `BEFORE_REMOVE_LIQUIDITY_ORDER` or `AFTER_REMOVE_LIQUIDITY_ORDER` enters a permanent revert state, every call to `removeLiquidity` reverts. LP shares become unclaimable; all token principal held in `binTotals.scaledToken0` / `scaledToken1` is permanently inaccessible. This satisfies the "direct loss of user principal" and "unusable withdraw/liquidity flows" impact gates.

If the broken extension is registered only for `BEFORE_SWAP_ORDER` / `AFTER_SWAP_ORDER`, swaps are permanently disabled, breaking core pool functionality and making the pool insolvent from a utility standpoint.

---

### Likelihood Explanation

Production extensions (`OracleValueStopLossExtension`, `PriceVelocityGuardExtension`) depend on external oracle feeds. If the underlying feed is deprecated, the oracle contract is upgraded to an incompatible interface, or the feed's heartbeat lapses permanently, the extension will revert on every invocation. This is a realistic operational failure, not a malicious setup assumption. The pool admin has no recourse because the extension address is immutable and the factory provides no removal path.

---

### Recommendation

1. Add an `emergencyDisableExtension(uint256 slot)` function callable by the factory owner only when the pool is at `pauseLevel == 2` (protocol pause). This mirrors the external report's recommendation of an owner-only, paused-state removal function.
2. Alternatively, wrap each `CallExtension.callExtension` invocation in a try/catch and emit a `ExtensionReverted` event, allowing the pool to degrade gracefully rather than brick.
3. At minimum, add `whenNotPaused` to `removeLiquidity` so that a protocol pause at least signals the broken state, and pair it with an upgrade path for extension slots.

---

### Proof of Concept

1. Pool is deployed with `OracleValueStopLossExtension` at `EXTENSION_1`, registered in `BEFORE_REMOVE_LIQUIDITY_ORDER = 1` (slot index 1).
2. The oracle feed the extension reads is deprecated; the feed contract is upgraded and the old interface reverts.
3. LP calls `removeLiquidity(owner, salt, deltas, extensionData)`.
4. Pool calls `_beforeRemoveLiquidity` → `_callExtensionsInOrder(BEFORE_REMOVE_LIQUIDITY_ORDER, ...)` → `CallExtension.callExtension(EXTENSION_1, ...)` → extension calls deprecated oracle → reverts.
5. `removeLiquidity` reverts. LP retries — same result every block.
6. Pool admin calls `pausePool` → `pauseLevel = 1`. LP retries `removeLiquidity` — still reverts (no `whenNotPaused` on `removeLiquidity`).
7. Factory owner calls `protocolPausePool` → `pauseLevel = 2`. LP retries — still reverts.
8. No further recourse exists. All LP principal in the pool is permanently locked. [8](#0-7) [4](#0-3)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L17-35)
```text
  address internal immutable EXTENSION_1;
  address internal immutable EXTENSION_2;
  address internal immutable EXTENSION_3;
  address internal immutable EXTENSION_4;
  address internal immutable EXTENSION_5;
  address internal immutable EXTENSION_6;
  address internal immutable EXTENSION_7;
  /// @dev Order of extension calls for before add liquidity.
  uint256 internal immutable BEFORE_ADD_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for after add liquidity.
  uint256 internal immutable AFTER_ADD_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for before remove liquidity.
  uint256 internal immutable BEFORE_REMOVE_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for after remove liquidity.
  uint256 internal immutable AFTER_REMOVE_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for before swap.
  uint256 internal immutable BEFORE_SWAP_ORDER;
  /// @dev Order of extension calls for after swap.
  uint256 internal immutable AFTER_SWAP_ORDER;
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

**File:** metric-core/contracts/ExtensionCalling.sol (L118-129)
```text
  function _beforeRemoveLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_REMOVE_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeRemoveLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
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
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L460-471)
```text
  function pausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0) revert InvalidPauseTransition(cur, 1);
    IMetricOmmPoolFactoryActions(pool).setPause(1);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function unpausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 1) revert InvalidPauseTransition(cur, 0);
    IMetricOmmPoolFactoryActions(pool).setPause(0);
  }
```
