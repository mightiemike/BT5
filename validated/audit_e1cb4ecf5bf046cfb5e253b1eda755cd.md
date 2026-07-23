### Title
Missing Deadline Parameter in `addLiquidityExactShares` / `addLiquidityWeighted` Allows Stale Liquidity Additions at Unfavorable Oracle Prices — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` exposes `addLiquidityExactShares` and `addLiquidityWeighted` with no deadline/expiry guard. A transaction submitted when the oracle price is favorable can sit in the mempool and execute arbitrarily later at a materially different oracle price, causing the LP to deposit at a ratio they never intended and receive an LP position worth less than the tokens they paid.

---

### Finding Description

`MetricOmmSimpleRouter` consistently applies `_checkDeadline(params.deadline)` before every swap entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). [1](#0-0) [2](#0-1) 

`MetricOmmPoolLiquidityAdder`, by contrast, has no such check on any of its entry points:

```solidity
function addLiquidityExactShares(
    address pool, address owner, uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(...);   // ← no deadline check
}
``` [3](#0-2) [4](#0-3) [5](#0-4) 

`addLiquidityWeighted` does include a `_validateBinAndBinPosition` check, but this only verifies the pool's current bin cursor falls within caller-supplied integer bounds — it does not bound the oracle price or the time of execution. [6](#0-5) 

The pool fetches a live oracle price at execution time via `_getBidAndAskPriceX64()` and uses it to compute the exact token amounts owed for the requested shares. [7](#0-6) 

Because the oracle price is consumed at execution time and there is no deadline, a transaction that was economically rational at submission can execute at an arbitrarily later oracle price.

---

### Impact Explanation

When a pending `addLiquidityExactShares` or `addLiquidityWeighted` transaction executes after a significant oracle price move:

1. The pool's bin cursor has shifted to reflect the new price.
2. The token amounts required for the same share count are now computed against the new oracle mid-price.
3. The LP receives shares priced at the new oracle level but may have submitted the transaction expecting the old level.
4. The LP position's fair value at the new price is lower than the tokens deposited — a direct loss of user principal.

The `maxAmountToken0` / `maxAmountToken1` caps prevent the pool from pulling *more* tokens than the user approved, but they do not prevent the user from depositing at an unfavorable price within those caps. [8](#0-7) 

---

### Likelihood Explanation

Network congestion, gas price spikes, or deliberate transaction ordering by block builders can delay a liquidity transaction by minutes to hours. Metric OMM pools are oracle-priced (Pyth Lazer / Chainlink Data Streams), so prices can move several percent in that window. The attack requires no special privilege — any LP using the periphery adder is exposed.

---

### Recommendation

Add a `uint256 deadline` parameter to both `addLiquidityExactShares` overloads and both `addLiquidityWeighted` overloads, and revert if `block.timestamp > deadline`, mirroring the pattern already used in `MetricOmmSwapRouterBase._checkDeadline`.

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    uint256 deadline,          // ← add
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    if (block.timestamp > deadline) revert DeadlineExpired();
    ...
}
```

---

### Proof of Concept

1. ETH/USDC pool; oracle price = $2 000. LP submits `addLiquidityExactShares` with `shares=[1 000]`, `maxAmountToken0 = 1 000 USDC`, `maxAmountToken1 = 0.5 ETH` (worth $1 000 at current price, total $2 000 deposit).
2. Transaction sits in the mempool for 30 minutes during congestion.
3. Oracle price drops to $1 500. The pool's bin cursor shifts; the same 1 000 shares now require a different token ratio (more USDC, less ETH) — both within the user's caps.
4. Transaction executes: the pool pulls, say, 900 USDC + 0.4 ETH (≈ $1 500 at new price) for 1 000 shares.
5. The LP position is immediately worth $1 500 at the new oracle price, but the LP paid $1 500 in tokens — however, the position's composition is now skewed toward the depreciating asset, and any immediate removal would yield less than deposited due to the bin cursor shift and rounding against the user.
6. Had a deadline of `block.timestamp + 5 minutes` been enforced at submission, the transaction would have reverted and the LP could resubmit at the new price with updated caps.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-68)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-93)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L71-81)
```text
  function addLiquidityExactShares(
    address pool,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateDeltas(deltas);
    return _addLiquidity(pool, msg.sender, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L88-116)
```text
  function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-167)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L263-286)
```text
  function _validateBinAndBinPosition(
    address pool,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition
  ) internal view {
    if (minimalCurBin > maximalCurBin) {
      revert CursorOutOfBounds(0, 0, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }

    (, int8 curBinIdx, uint104 curPosInBin,,,) = PoolStateLibrary._slot0(pool);

    int256 curBin = curBinIdx;
    if (curBin < minimalCurBin || curBin > maximalCurBin) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
    if (curBinIdx == minimalCurBin && curPosInBin < minimalPosition) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
    if (curBinIdx == maximalCurBin && curPosInBin > maximalPosition) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L804-813)
```text
  function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
    } catch (bytes memory reason) {
      revert PriceProviderFailed(reason);
    }
  }
```
