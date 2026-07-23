### Title
Immutable Extension Guards Have No Disable Mechanism — A Permanently-Reverting Oracle Extension Locks LP Withdrawals and Swaps — (`metric-core/contracts/ExtensionCalling.sol`)

### Summary

All extension addresses and their invocation orders are stored as `internal immutable` variables in `ExtensionCalling.sol`. There is no function anywhere in `MetricOmmPool` or `MetricOmmPoolFactory` to disable, replace, or bypass a configured extension after pool deployment. Oracle-dependent extensions (`OracleValueStopLossExtension`, `PriceVelocityGuardExtension`) registered on the `beforeRemoveLiquidity` or `beforeSwap` hook can enter a permanently-reverting state if their oracle becomes stale, returns zero, or is manipulated to trip the guard threshold. When that happens, every call to `removeLiquidity` or `swap` reverts with no recovery path, permanently locking LP principal in the pool.

### Finding Description

`ExtensionCalling.sol` bakes all seven extension slots and all six hook-order words into EVM immutables at construction time:

```solidity
address internal immutable EXTENSION_1;
// … EXTENSION_2 … EXTENSION_7
uint256 internal immutable BEFORE_REMOVE_LIQUIDITY_ORDER;
uint256 internal immutable AFTER_REMOVE_LIQUIDITY_ORDER;
uint256 internal immutable BEFORE_SWAP_ORDER;
// …
``` [1](#0-0) 

`_callExtensionsInOrder` iterates the packed order word and calls each extension with no try/catch — a revert from any extension propagates to the caller:

```solidity
function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;
    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);   // ← no try/catch
      order >>= 3;
    }
}
``` [2](#0-1) 

`_beforeRemoveLiquidity` and `_beforeSwap` both route through this function: [3](#0-2) [4](#0-3) 

`MetricOmmPool` exposes no function to change or nullify an extension. The only mutable guard is `setPriceProvider` (for the price oracle) and `setPause` (which only blocks swaps, not `removeLiquidity`): [5](#0-4) [6](#0-5) 

`MetricOmmPoolFactory` similarly has no path to update extension addresses or orders post-deployment. The factory's `protocolPausePool` / `pausePool` only set `pauseLevel`, which is checked only by the `whenNotPaused` modifier on `swap` — `removeLiquidity` carries no such modifier: [7](#0-6) 

The periphery ships two oracle-dependent guard extensions — `OracleValueStopLossExtension` and `PriceVelocityGuardExtension` — that are designed to revert when oracle-derived values cross configured thresholds. If either is registered on `BEFORE_REMOVE_LIQUIDITY_ORDER` and its oracle feed becomes stale, returns zero, or is manipulated to permanently satisfy the guard condition, every `removeLiquidity` call reverts. Because the extension address is immutable and no disable path exists, the pool is bricked for LPs with no on-chain remedy.



### Impact Explanation

LP principal is permanently locked inside the pool. LPs cannot call `removeLiquidity` because the hook reverts on every attempt. Swaps are equally blocked if the extension is on `BEFORE_SWAP_ORDER`. The pool's token balances remain non-zero and non-claimable — a direct loss of user principal above any Sherlock threshold. This matches the allowed impact gate: "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."

### Likelihood Explanation

Any pool that legitimately deploys an oracle-based guard extension on the remove-liquidity or swap hook is exposed. Oracle staleness, a zero-price edge case, or a sustained market move that permanently trips a stop-loss threshold are all realistic, non-malicious triggers. The pool admin cannot mitigate after the fact because no disable path exists. Likelihood is medium-to-high for pools using these extensions in volatile or low-liquidity oracle environments.

### Recommendation

1. **Add an emergency extension bypass in the factory**: introduce a factory-owner-controlled flag (or a `pauseExtensions` level) that causes `_callExtensionsInOrder` to skip all hooks, analogous to how `VaderPoolV2` allows governance to turn off `GasThrottle`.
2. **Alternatively, wrap extension calls in try/catch with a configurable fail-open/fail-closed flag** so a reverting extension degrades gracefully rather than bricking the pool.
3. **At minimum**, ensure `removeLiquidity` is never gated by oracle-dependent extensions, or add a factory-level emergency `removeLiquidity` bypass that skips extension hooks when `pauseLevel == 2`.

### Proof of Concept

1. Pool is deployed with `OracleValueStopLossExtension` registered at index 1 in `BEFORE_REMOVE_LIQUIDITY_ORDER`.
2. The extension's oracle feed becomes stale (returns a price of 0 or a value that permanently satisfies the stop-loss condition).
3. LP calls `removeLiquidity(owner, salt, deltas, extensionData)`.
4. Pool calls `_beforeRemoveLiquidity` → `_callExtensionsInOrder(BEFORE_REMOVE_LIQUIDITY_ORDER, ...)` → `CallExtension.callExtension(EXTENSION_1, ...)`.
5. `OracleValueStopLossExtension.beforeRemoveLiquidity` reads the stale oracle and reverts.
6. The revert propagates; LP's transaction fails.
7. Because `EXTENSION_1` is `immutable` and no factory or pool function can change it, every future `removeLiquidity` call reverts identically.
8. LP funds are permanently locked with no on-chain recovery path. [2](#0-1) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
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

**File:** metric-core/contracts/MetricOmmPool.sol (L455-461)
```text
  function setPause(uint8 newLevel) external onlyFactory {
    if (newLevel > 2) revert InvalidPauseLevel();
    if (newLevel == pauseLevel) return;
    uint8 prev = pauseLevel;
    pauseLevel = newLevel;
    emit PauseLevelUpdated(prev, newLevel);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L477-480)
```text
  function setPriceProvider(address newPriceProvider) external onlyFactory {
    priceProvider = newPriceProvider;
    emit PriceProviderUpdated(newPriceProvider);
  }
```
