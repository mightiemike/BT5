### Title
Liquidity Operations Bypass the Pool Pause Guard, Enabling LP Fund Extraction During Emergency Pause — (File: `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`swap()` is gated by `whenNotPaused`, but `addLiquidity()` and `removeLiquidity()` carry no such guard. When the pool is paused at any level (`pauseLevel != 0`), swaps are correctly blocked, yet any LP can still call `removeLiquidity()` to extract their share of pool assets. This is a direct structural analog to the Ion Protocol bug: one code path (protocol liquidation / liquidity removal) bypasses the pause guard that was meant to freeze all pool operations.

---

### Finding Description

`_checkNotPaused()` reverts whenever `pauseLevel != 0`: [1](#0-0) 

The `whenNotPaused` modifier wraps this check and is applied **only** to `swap()`: [2](#0-1) [3](#0-2) 

`addLiquidity()` and `removeLiquidity()` carry only `nonReentrant` — no pause check: [4](#0-3) [5](#0-4) 

`pauseLevel` is documented as having three states (0 = active, 1 = paused by admin, 2 = paused by protocol), and transitions are enforced by the factory: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

A pause is typically triggered in response to an anomaly — oracle manipulation, incorrect bin accounting, or a security incident. The intent is to freeze all pool operations while the issue is investigated or remediated. Because `removeLiquidity()` is not gated, any LP can:

1. Observe the pause event on-chain.
2. Immediately call `removeLiquidity()` to withdraw their proportional share of `binTotals.scaledToken0` / `scaledToken1`.
3. Exit before the issue is resolved, while other LPs (and swappers) remain locked out.

If the pause was triggered because bin accounting was corrupted (e.g., by a prior oracle-price-manipulation swap that moved tokens between bins at incorrect prices), the per-bin share ratios used by `LiquidityLib.removeLiquidity()` may already reflect the corrupted state. An LP who removes liquidity first extracts a disproportionate share of the remaining assets, leaving the pool insolvent for remaining LPs whose claims can no longer be fully covered by `binTotals`.

This matches the allowed impact gate: **pool insolvency — balances fail to cover LP claims**.

---

### Likelihood Explanation

- The trigger (a pause event) is an on-chain state change that is immediately visible to any monitoring bot or MEV searcher.
- No special privilege is required: any address that holds LP shares in any bin can call `removeLiquidity(owner, salt, deltas, extensionData)` where `owner == msg.sender` (enforced by `if (msg.sender != owner) revert NotPositionOwner()`).
- The window between the pause transaction and remediation is the attack window; in practice this can be multiple blocks.

Likelihood: **Medium** (requires a pause event, but the exploit is trivially executable by any LP once the pause is observed).

---

### Recommendation

Add the `whenNotPaused` modifier to both `addLiquidity()` and `removeLiquidity()`, consistent with how `swap()` is protected:

```solidity
function addLiquidity(...) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) ...

function removeLiquidity(...) external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY) ...
```

This mirrors the fix applied in Ion Protocol PR #36, which extended the pause to cover all pool-state-mutating operations rather than only one entry point.

---

### Proof of Concept

1. Pool is deployed with a mutable price provider. An attacker manipulates the oracle to return an extreme bid/ask, executes a swap that drains token0 from bins, and the admin pauses the pool (`pauseLevel = 1`) to stop further damage.
2. A monitoring bot observes the `PauseLevelUpdated` event in the same block.
3. The bot calls `removeLiquidity(owner, salt, deltas, "")` with `deltas` covering all bins where the LP holds shares. The call succeeds — no `whenNotPaused` check exists.
4. `LiquidityLib.removeLiquidity()` computes the LP's share of the now-corrupted `binTotals` and transfers tokens out.
5. Remaining LPs attempt to call `removeLiquidity()` after the pause is lifted; the pool's `binTotals` no longer cover their claims, resulting in a revert or under-payment — pool insolvency.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L71-72)
```text
  /// @dev 0 = active, 1 = paused by admin, 2 = paused by protocol. Transitions enforced by factory.
  uint8 internal pauseLevel;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L174-177)
```text
  modifier whenNotPaused() {
    _checkNotPaused();
    _;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
```
