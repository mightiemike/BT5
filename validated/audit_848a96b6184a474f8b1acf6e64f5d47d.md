### Title
Immutable Extension Registry Cannot Be Pruned — A Reverting Oracle-Based Extension Permanently Bricks `removeLiquidity`, Locking LP Funds - (File: `metric-core/contracts/ExtensionCalling.sol`)

---

### Summary

Every pool's extension set is baked into contract immutables at deployment. No factory or admin path exists to remove or disable a single extension after the fact. If any extension configured on the `BEFORE_REMOVE_LIQUIDITY_ORDER` or `AFTER_REMOVE_LIQUIDITY_ORDER` slot begins reverting — for example, an oracle-based guard (`OracleValueStopLossExtension`, `PriceVelocityGuardExtension`) whose feed goes stale or is deprecated — `removeLiquidity` reverts on every call, permanently locking LP principal.

---

### Finding Description

**Immutable extension slots — no removal path**

In `ExtensionCalling.sol`, all seven extension addresses and all six call-order words are stored as Solidity `immutable` variables: [1](#0-0) 

They are written once in the constructor and can never be changed: [2](#0-1) 

`MetricOmmPoolFactory` exposes no `setExtension`, `disableExtension`, or equivalent function anywhere in its interface. The only post-deployment pool-level setters are `setPoolFees`, `setPause`, `setBinAdditionalFees`, and `setPriceProvider` — none touch extensions: [3](#0-2) 

**Revert propagation through `_callExtensionsInOrder`**

Every pool action that has a configured hook calls `_callExtensionsInOrder`, which iterates the packed order word and calls each extension in sequence. A revert from any extension propagates directly to the caller: [4](#0-3) 

**`removeLiquidity` calls both before- and after-hooks**

`MetricOmmPool.removeLiquidity` calls `_beforeRemoveLiquidity` before the accounting update and `_afterRemoveLiquidity` after it. Either hook reverting aborts the entire withdrawal: [5](#0-4) 

The before-hook dispatches to the packed `BEFORE_REMOVE_LIQUIDITY_ORDER`: [6](#0-5) 

**Oracle-based extensions as the concrete trigger**

The periphery ships `OracleValueStopLossExtension` and `PriceVelocityGuardExtension` — both read live oracle prices and are designed to revert when their guard condition fires. If either is wired into a remove-liquidity hook slot and its underlying oracle feed is deprecated, returns a stale timestamp, or begins returning bad data, the extension reverts on every call. Because the extension address is immutable and no bypass exists, `removeLiquidity` is permanently bricked for that pool.



This is the direct structural analog to the external report: a registry entry (covered pool / extension slot) that can be added but never removed, where an external oracle failure converts a guard into a permanent DoS on fund withdrawal.

---

### Impact Explanation

**Severity: High**

LP principal is locked inside the pool. `removeLiquidity` reverts on every call as long as the broken extension is wired to a remove-liquidity hook. There is no admin escape hatch — the factory cannot disable the extension, the pool admin cannot bypass it, and the protocol owner has no override. Funds remain inaccessible until the oracle feed recovers, which may never happen if the feed is deprecated.

---

### Likelihood Explanation

**Severity: Low**

The condition requires (a) a pool to be deployed with an oracle-based extension on a remove-liquidity hook slot, and (b) the oracle feed to become permanently stale or deprecated. Both conditions are plausible in production (oracle deprecations do occur), but their conjunction is uncommon.

---

### Recommendation

1. **Add an emergency extension bypass in the factory**: introduce a per-pool `disabledExtensions` bitmask (stored in the factory, not the pool) that `_callExtensionsInOrder` checks before each call, allowing the pool admin or protocol owner to skip a broken extension slot without redeployment.

2. **Alternatively, store extension addresses in mutable factory storage** rather than pool immutables, so the factory can point a slot to a no-op pass-through address.

3. **Oracle-based extensions should fail open on `removeLiquidity`**: guards that are appropriate for `beforeSwap` (where a bad price is dangerous) should not be applied to `removeLiquidity` (where blocking withdrawal locks principal). Extension authors should distinguish hook contexts.

---

### Proof of Concept

1. Pool is deployed with `OracleValueStopLossExtension` (or `PriceVelocityGuardExtension`) registered at `EXTENSION_1` and `BEFORE_REMOVE_LIQUIDITY_ORDER = 0x1` (slot 1 fires first on remove-liquidity).
2. The oracle feed used by the extension is deprecated or begins returning a stale `refTime`.
3. The extension's oracle read reverts or the guard condition fires unconditionally.
4. LP calls `removeLiquidity(owner, salt, deltas, extensionData)`.
5. Pool calls `_beforeRemoveLiquidity` → `_callExtensionsInOrder(BEFORE_REMOVE_LIQUIDITY_ORDER, ...)` → `CallExtension.callExtension(EXTENSION_1, ...)` → extension reverts.
6. `removeLiquidity` reverts. LP shares remain in `_positionBinShares`; underlying tokens remain in the pool. No admin path exists to recover them. [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L405-471)
```text
  // ============ External: pool admin ============

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();

    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );

    c.adminSpreadFeeE6 = newAdminSpreadFeeE6;
    c.adminNotionalFeeE8 = newAdminNotionalFeeE8;
    poolFeeConfig[pool] = c;

    IMetricOmmPoolFactoryActions(pool)
      .setPoolFees(c.protocolSpreadFeeE6 + c.adminSpreadFeeE6, c.protocolNotionalFeeE8 + c.adminNotionalFeeE8);
    emit PoolAdminSpreadFeeUpdated(pool, newAdminSpreadFeeE6);
    emit PoolAdminNotionalFeeUpdated(pool, newAdminNotionalFeeE8);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
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
