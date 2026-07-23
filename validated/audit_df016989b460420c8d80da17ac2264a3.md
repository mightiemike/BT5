### Title
Missing Deadline Check in `MetricOmmPoolLiquidityAdder` Allows Pending Liquidity Transactions to Execute at Stale Oracle-Driven Composition — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` exposes `addLiquidityExactShares` and `addLiquidityWeighted` with no deadline parameter. A validator can hold a pending liquidity transaction and execute it after the oracle price has moved, forcing the LP to deposit at a bin composition that is materially different from what they intended, causing a direct loss of principal.

---

### Finding Description

The `MetricOmmSimpleRouter` correctly guards every swap entry point with `_checkDeadline()`: [1](#0-0) 

All four router functions call it at entry: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

`MetricOmmPoolLiquidityAdder` has no equivalent guard anywhere. None of its public entry points accept or check a deadline: [6](#0-5) [7](#0-6) 

The `addLiquidityWeighted` path is the most dangerous. It executes a probe call to determine the current token composition (`need0`, `need1`) and then scales the user's weight vector to fit within `maxAmountToken0`/`maxAmountToken1`: [8](#0-7) 

The probe composition is entirely determined by the pool's cursor position at execution time, which is driven by the oracle price at that moment. The `minimalCurBin`/`maximalCurBin` bounds only guard against cursor *index* drift, not against oracle price movement within a bin: [9](#0-8) 

The `maxAmountToken0`/`maxAmountToken1` caps are absolute amount ceilings, not price-composition guards. They do not prevent the user from depositing the full cap of a token that has depreciated.

---

### Impact Explanation

A validator holds the pending `addLiquidityWeighted` transaction until the oracle price has moved adversely (e.g., token0 depreciates). At the new oracle price the pool cursor has shifted, so the probe returns a composition heavily weighted toward token0. The scaling step then deposits up to `maxAmountToken0` of the now-cheaper token. The LP receives shares priced at the new oracle mid, but paid in tokens at the old (higher) value they approved. The difference is a direct, unrecoverable loss of principal for the LP. In the worst case the LP deposits the full `maxAmountToken0` of a token that has lost significant value since the transaction was broadcast.

---

### Likelihood Explanation

On Ethereum and Base, validators routinely observe the mempool and can selectively delay transactions. On HyperEVM the same applies to block proposers. No special privilege beyond block-production ordering is required. The attack is profitable whenever the oracle price moves enough between broadcast and inclusion that the composition shift exceeds the attacker's gas cost. Liquidity additions are typically large transactions, making the expected profit positive even for modest price moves.

---

### Recommendation

Add a `uint256 deadline` parameter to all public entry points of `MetricOmmPoolLiquidityAdder` and call the same `_checkDeadline` pattern used in `MetricOmmSwapRouterBase` before any pool interaction:

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
    if (block.timestamp > deadline) revert TransactionExpired(deadline, block.timestamp);
    ...
}
```

Apply the same change to both overloads of `addLiquidityExactShares` and both overloads of `addLiquidityWeighted`.

---

### Proof of Concept

1. LP calls `addLiquidityWeighted` with `maxAmountToken0 = 10_000e18`, `maxAmountToken1 = 10_000e6` (USDC), targeting a 50/50 deposit at the current oracle price of $1.00/token0.
2. Validator observes the transaction in the mempool and withholds it.
3. Oracle price of token0 drops to $0.50. Pool cursor shifts; the probe now returns `need0 = 20_000e18`, `need1 = 1e6` (almost entirely token0).
4. Scaling: `scaleWad0 = 10_000e18 * WAD / 20_000e18 = 0.5 WAD`; `scaleWad1 = 10_000e6 * WAD / 1e6 = 10_000 WAD`; `scaleWad = 0.5 WAD`.
5. Validator includes the transaction. LP deposits `~10_000e18` token0 (worth $5,000 at new price) and `~0.5e6` USDC — a total deposit worth ~$5,000.50 instead of the intended ~$10,000.
6. LP has lost ~$5,000 of value relative to their intent, with no recourse. [10](#0-9)

### Citations

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L91-94)
```text
  function _checkDeadline(uint256 deadline) internal view {
    // forge-lint: disable-next-line(block-timestamp)
    if (block.timestamp > deadline) revert IMetricOmmSimpleRouter.TransactionExpired(deadline, block.timestamp);
  }
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L226-243)
```text
  function _scaleWeightsToShares(LiquidityDelta calldata w, uint256 max0, uint256 max1, uint256 need0, uint256 need1)
    internal
    pure
    returns (LiquidityDelta memory out)
  {
    uint256 scaleWad0 = need0 == 0 ? type(uint256).max : Math.mulDiv(max0, WAD, need0);
    uint256 scaleWad1 = need1 == 0 ? type(uint256).max : Math.mulDiv(max1, WAD, need1);
    uint256 scaleWad = scaleWad0 < scaleWad1 ? scaleWad0 : scaleWad1;

    uint256 n = w.binIdxs.length;
    out.binIdxs = new int256[](n);
    out.shares = new uint256[](n);
    for (uint256 i; i < n; i++) {
      out.binIdxs[i] = w.binIdxs[i];
      out.shares[i] = Math.mulDiv(w.shares[i], scaleWad, WAD);
      if (w.shares[i] != 0 && out.shares[i] == 0) revert SharesRoundedToZero();
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
