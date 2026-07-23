### Title
Immutable Extension Configuration Permanently Bricks Core Pool Operations When Extension Dependency Fails — (File: `metric-core/contracts/ExtensionCalling.sol`)

---

### Summary

All pool extension addresses and their hook-order bitmasks are stored as **immutable** constructor arguments in `ExtensionCalling`. The `_callExtensionsInOrder` dispatcher has no try/catch wrapper. If any registered extension's external dependency (oracle, allowlist registry, stop-loss feed) permanently reverts or enters a state that causes the extension to always revert, every pool operation that routes through that hook is permanently bricked with no admin recovery path.

---

### Finding Description

`ExtensionCalling` stores seven extension slots and six hook-order bitmasks as `immutable` values set once at construction: [1](#0-0) 

The dispatcher iterates the packed bitmask and calls each extension with no error isolation: [2](#0-1) 

`CallExtension.callExtension` is a raw delegatecall/call with no try/catch. If the callee reverts, the revert propagates up through `_callExtensionsInOrder` and aborts the entire pool operation.

`MetricOmmPool` wires these hooks into every user-facing action:

- `addLiquidity` → `_beforeAddLiquidity` / `_afterAddLiquidity`
- `removeLiquidity` → `_beforeRemoveLiquidity` / `_afterRemoveLiquidity`
- `swap` → `_beforeSwap` / `_afterSwap` [3](#0-2) [4](#0-3) [5](#0-4) 

The periphery ships three guard extensions that each depend on an external state source:

- `OracleValueStopLossExtension` — reads an oracle price and reverts if the value crosses a threshold
- `PriceVelocityGuardExtension` — reads prior price observations and reverts if velocity exceeds a limit
- `DepositAllowlistExtension` / `SwapAllowlistExtension` — read an external allowlist registry



If any of these external dependencies permanently enters a failure state after pool deployment — oracle feed deprecated, allowlist registry self-destructed, stop-loss threshold permanently breached by a market event — the extension will revert on every invocation. Because the extension addresses and hook orders are immutable, there is no `setExtension`, `removeExtension`, or emergency bypass available to the factory or pool admin. The only admin lever is `setPause`, which only gates `swap` (via `whenNotPaused`) and does not help LPs who need to call `removeLiquidity`. [6](#0-5) [7](#0-6) 

The direct analog to the Amphora finding: Convex pool shutdown → `depositERC20` permanently reverts because the collateral type cannot be updated. Here: extension dependency failure → `removeLiquidity` (or `swap`) permanently reverts because the extension configuration cannot be updated.

---

### Impact Explanation

**Worst case — `beforeRemoveLiquidity` or `afterRemoveLiquidity` extension permanently reverts:** all LP positions are frozen. LPs cannot recover their principal. This is a direct loss of user principal above any Sherlock threshold.

**Secondary case — `beforeSwap` or `afterSwap` extension permanently reverts:** all swaps are bricked. The pool becomes a one-way liquidity sink; LPs can add but not trade, and the pool's fee-generating purpose is destroyed.

Both cases satisfy the allowed impact gate: *"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."*

---

### Likelihood Explanation

The trigger is an external event, not a privileged action — identical in character to the Convex pool shutdown in the seed report. Concrete scenarios:

1. `OracleValueStopLossExtension` references a Chainlink feed that is deprecated or returns a permanently stale answer that crosses the stop-loss threshold.
2. `PriceVelocityGuardExtension` references a price observation contract that is upgraded or self-destructed.
3. `SwapAllowlistExtension` or `DepositAllowlistExtension` references an allowlist registry whose admin key is lost, permanently locking or unlocking access in a way that causes the extension to always revert.

None of these require a privileged actor to act maliciously — they are ordinary operational risks for any protocol that depends on external contracts.

---

### Recommendation

1. **Wrap extension calls in try/catch** inside `_callExtensionsInOrder` and emit a failure event rather than propagating the revert, or provide a configurable "fail-open / fail-closed" flag per extension slot.
2. **Make extension slots mutable** (factory-gated setter, analogous to `setPriceProvider`) so the factory admin can remove or replace a broken extension without redeploying the pool.
3. **Separate `removeLiquidity` from guard extensions** — at minimum, ensure that LP withdrawal hooks cannot be permanently blocked by a stop-loss or allowlist guard that was intended only for swap protection.

---

### Proof of Concept

```
Scenario: OracleValueStopLossExtension registered for beforeRemoveLiquidity

1. Pool deployed with OracleValueStopLossExtension at EXTENSION_1,
   BEFORE_REMOVE_LIQUIDITY_ORDER = 0x1 (call extension 1 first).

2. LPs add liquidity normally. Pool accumulates token0 and token1.

3. External oracle feed used by OracleValueStopLossExtension is deprecated
   by the feed provider (e.g., Chainlink removes the aggregator).
   The extension now reverts on every call.

4. LP calls removeLiquidity(owner, salt, deltas, extensionData):
   → _beforeRemoveLiquidity is called
   → _callExtensionsInOrder(BEFORE_REMOVE_LIQUIDITY_ORDER, ...)
   → CallExtension.callExtension(EXTENSION_1, ...) reverts
   → removeLiquidity reverts

5. No admin function exists to update EXTENSION_1 or BEFORE_REMOVE_LIQUIDITY_ORDER
   (both are immutable). setPause only gates swap, not removeLiquidity.

6. LP funds are permanently locked in the pool.
``` [2](#0-1) [8](#0-7) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L174-177)
```text
  modifier whenNotPaused() {
    _checkNotPaused();
    _;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```
