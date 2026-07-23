### Title
Missing Deadline Check in `MetricOmmPoolLiquidityAdder` Allows Pending Liquidity Transactions to Execute at Stale Oracle Prices — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

The `MetricOmmPoolLiquidityAdder` contract exposes `addLiquidityExactShares` and `addLiquidityWeighted` without any deadline parameter. A transaction submitted to the mempool with a low gas fee can sit pending for hours or days; when it eventually executes, the oracle price may have moved materially, causing the user to deposit tokens into a position whose composition and immediate value differ substantially from what they intended.

---

### Finding Description

`MetricOmmSimpleRouter` correctly gates every swap entry-point with `_checkDeadline(params.deadline)`: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

`MetricOmmPoolLiquidityAdder` inherits only from `PeripheryPayments` — not from `MetricOmmSwapRouterBase` — and therefore has no access to `_checkDeadline`. Neither `addLiquidityExactShares` overload nor either `addLiquidityWeighted` overload accepts or enforces a deadline: [5](#0-4) [6](#0-5) [7](#0-6) 

Because the Metric OMM pool is fully oracle-anchored — every swap and liquidity operation prices tokens against the live oracle bid/ask at execution time — the token composition of a newly minted position is entirely determined by the oracle price at the moment the transaction lands on-chain: [8](#0-7) 

The `maxAmountToken0` / `maxAmountToken1` caps only bound how many tokens the pool may pull; they do not constrain the oracle price at which those tokens are converted into shares. A user who signs a transaction at oracle price P is not protected if the transaction executes at oracle price P′.

`addLiquidityWeighted` does include cursor-position bounds (`minimalCurBin` / `maximalCurBin`): [9](#0-8) 

However, these bounds guard against pool-cursor drift caused by other swaps, not against oracle price movement. The oracle price can shift by 20–50 % while the pool cursor remains within a user-specified bin range, so the cursor check does not substitute for a deadline.

---

### Impact Explanation

In an oracle-anchored pool, the ratio of token0 to token1 required per share is a direct function of the live oracle mid-price. If the oracle price moves adversely between transaction signing and execution:

- The user's position is minted at the new oracle price, not the intended one.
- Removing liquidity immediately returns tokens valued at the new oracle price; if that price is lower than the price at which the user intended to deposit, the user suffers a direct loss of principal relative to simply holding the tokens.
- For `addLiquidityExactShares` (no cursor bounds at all), there is no on-chain guard whatsoever beyond the token-amount caps.

The loss is proportional to the oracle price move and the deposit size — it can be material for large positions or volatile assets.

**Severity: Medium** — direct loss of user principal; no privileged actor required; standard mempool-delay scenario.

---

### Likelihood Explanation

- Any user who submits a liquidity transaction with a gas price below the prevailing base fee faces this exposure; this is routine during gas-price spikes.
- Oracle prices for volatile assets (e.g., ETH, BTC) routinely move 5–20 % within hours and 30–50 % within days — well within realistic mempool-delay windows.
- No attacker action is required; the loss occurs passively from normal market movement.

---

### Recommendation

Add a `uint256 deadline` parameter to all four public entry-points in `MetricOmmPoolLiquidityAdder` and enforce it at the top of each function, mirroring the pattern already used in `MetricOmmSimpleRouter`:

```solidity
// In MetricOmmPoolLiquidityAdder
function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
+   uint256 deadline,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
+   if (block.timestamp > deadline) revert DeadlineExpired();
    ...
}
```

Apply the same change to both `addLiquidityWeighted` overloads and the second `addLiquidityExactShares` overload.

---

### Proof of Concept

1. Alice observes oracle price P = 2 000 USDC/ETH and calls `addLiquidityExactShares` with `maxAmountToken0 = 1 ETH`, `maxAmountToken1 = 2 000 USDC`, targeting bin 0 with N shares. She submits with a gas price below the current base fee.
2. The transaction sits in the mempool for 48 hours. The oracle price drops to P′ = 1 000 USDC/ETH.
3. Gas fees fall; the transaction is included. The pool calls `_getBidAndAskPriceX64()`, which fetches the live oracle price P′.
4. Alice's N shares are minted at P′. Her position now requires ~1 ETH + ~1 000 USDC (the token1 leg is halved relative to her expectation).
5. Alice immediately calls `removeLiquidity`. She receives back ~1 ETH + ~1 000 USDC — 1 000 USDC less than she would have received had the transaction executed at P.
6. No attacker action was required; the loss is purely from the missing deadline allowing stale execution.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-131)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-155)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
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
