### Title
Permanently Broken Extension Irreversibly Locks LP Funds and Bricks All Pool Operations — (`metric-core/contracts/ExtensionCalling.sol`)

---

### Summary

`ExtensionCalling._callExtensionsInOrder` propagates any extension revert directly to the caller with no try/catch isolation. Because all seven extension slots are stored as **immutable** constructor arguments, there is no admin path to replace or bypass a broken extension. If any extension configured in `BEFORE_REMOVE_LIQUIDITY_ORDER` or `AFTER_REMOVE_LIQUIDITY_ORDER` permanently reverts, LPs can never withdraw their funds. The same failure in `BEFORE_SWAP_ORDER`/`AFTER_SWAP_ORDER` permanently bricks trading.

---

### Finding Description

`CallExtension.callExtension` performs a raw low-level call and, on failure, unconditionally reverts — either bubbling the extension's revert reason or throwing `ExtensionCallFailed()`: [1](#0-0) 

`_callExtensionsInOrder` loops over every slot in the packed `order` word and calls `callExtension` for each non-zero index with no isolation between iterations: [2](#0-1) 

All six hook dispatch functions (`_beforeAddLiquidity`, `_afterAddLiquidity`, `_beforeRemoveLiquidity`, `_afterRemoveLiquidity`, `_beforeSwap`, `_afterSwap`) delegate unconditionally to `_callExtensionsInOrder`: [3](#0-2) 

The extension addresses are stored as **immutable** constructor arguments (`EXTENSION_1` … `EXTENSION_7`) and the order bitmaps are likewise immutable: [4](#0-3) 

`MetricOmmPool` exposes no function to update, replace, or nullify any extension after deployment. The only admin knob is `setPause`, which only gates `swap()` via `whenNotPaused`: [5](#0-4) 

`removeLiquidity` carries **no** `whenNotPaused` guard, yet it calls both `_beforeRemoveLiquidity` and `_afterRemoveLiquidity`: [6](#0-5) 

Therefore, even a fully paused pool cannot allow LPs to exit if a remove-liquidity extension is broken.

---

### Impact Explanation

If any extension registered in a remove-liquidity or swap hook permanently reverts:

| Broken hook order | Blocked operation | Fund impact |
|---|---|---|
| `BEFORE_REMOVE_LIQUIDITY_ORDER` | `removeLiquidity` | LP principal permanently locked |
| `AFTER_REMOVE_LIQUIDITY_ORDER` | `removeLiquidity` | LP principal permanently locked |
| `BEFORE_SWAP_ORDER` / `AFTER_SWAP_ORDER` | `swap` | Pool unusable; LPs cannot exit via swap |
| `BEFORE_ADD_LIQUIDITY_ORDER` | `addLiquidity` | New deposits blocked |

Because extensions are immutable and `setPause` does not bypass extension calls on `removeLiquidity`, there is **no recovery path**. All LP funds held in the pool are permanently inaccessible. This matches the allowed impact gate: *broken core pool functionality causing loss of funds or unusable withdraw/liquidity flows*.

---

### Likelihood Explanation

Extensions are external contracts that may themselves depend on oracles (`OracleValueStopLossExtension`, `PriceVelocityGuardExtension`), allowlist registries (`DepositAllowlistExtension`, `SwapAllowlistExtension`), or other mutable state. Realistic triggers include:

1. An upgradeable extension contract receives a breaking upgrade (changed function selector, storage collision, logic bug).
2. An oracle dependency inside an extension becomes permanently stale or its contract is deprecated/self-destructed.
3. An allowlist registry contract is paused or decommissioned by its own admin.
4. The extension contract itself is hacked and self-destructed (proxy implementation wiped).

None of these require any action by the pool admin or an attacker against the pool itself — they are ordinary lifecycle events for external contracts. The pool has no circuit-breaker for this scenario.

---

### Recommendation

1. **Wrap each extension call in try/catch** inside `_callExtensionsInOrder`. On failure, either revert with a clear error (preserving the current strict behavior) **or** treat the failure as a hard block and allow governance to disable the broken slot.
2. **Add a mutable emergency bypass** — a factory-controlled flag per extension slot that skips that slot's call, allowing the pool admin to neutralize a broken extension without redeploying the pool.
3. **Extend `whenNotPaused` (or a separate emergency mode) to skip extension calls on `removeLiquidity`**, so LPs can always exit even if an extension is broken, analogous to how the Reserve Protocol mitigation separated the unregistration flow from the broken asset's own functions.

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension` (or any oracle-dependent extension) registered in `BEFORE_REMOVE_LIQUIDITY_ORDER`.
2. LPs add liquidity normally.
3. The oracle contract used by the extension is deprecated/upgraded and now reverts on every call.
4. Any LP calls `removeLiquidity(owner, salt, deltas, extensionData)`.
5. `_beforeRemoveLiquidity` → `_callExtensionsInOrder` → `CallExtension.callExtension(brokenExtension, data)` → extension reverts → `callExtension` reverts → `removeLiquidity` reverts.
6. The pool admin calls `setPause(1)` — this has no effect on `removeLiquidity` (no `whenNotPaused` guard).
7. There is no other admin function that can replace `EXTENSION_1`…`EXTENSION_7` or the order bitmaps.
8. LP funds are permanently locked.

### Citations

**File:** metric-core/contracts/libraries/CallExtension.sol (L8-17)
```text
  function callExtension(address extension, bytes memory data) internal {
    (bool success, bytes memory result) = extension.call(data);
    if (!success) {
      if (result.length > 0) {
        assembly ("memory-safe") {
          revert(add(result, 32), mload(result))
        }
      }
      revert ExtensionCallFailed();
    }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L17-51)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L118-130)
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

**File:** metric-core/contracts/MetricOmmPool.sol (L454-461)
```text
  /// @inheritdoc IMetricOmmPoolFactoryActions
  function setPause(uint8 newLevel) external onlyFactory {
    if (newLevel > 2) revert InvalidPauseLevel();
    if (newLevel == pauseLevel) return;
    uint8 prev = pauseLevel;
    pauseLevel = newLevel;
    emit PauseLevelUpdated(prev, newLevel);
  }
```
