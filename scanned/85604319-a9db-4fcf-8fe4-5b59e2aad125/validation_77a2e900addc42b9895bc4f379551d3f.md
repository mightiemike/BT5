### Title
Missing Minimum-Output Guard on `removeLiquidity()` While `addLiquidity` Path Is Protected — (`metric-core/contracts/MetricOmmPool.sol`, `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`addLiquidity` is exposed through a periphery wrapper (`MetricOmmPoolLiquidityAdder`) that enforces maximum-input slippage caps (`maxAmountToken0` / `maxAmountToken1`). `removeLiquidity` has no corresponding periphery wrapper and no minimum-output guard anywhere in its call path. An LP who simulates their removal and then submits the transaction can receive materially less than expected if swaps alter bin composition in the interim.

---

### Finding Description

`MetricOmmPoolLiquidityAdder` provides two protected entry points for adding liquidity:

- `addLiquidityExactShares` — enforces `maxAmountToken0` / `maxAmountToken1` in the callback before any token pull.
- `addLiquidityWeighted` — runs a probe-revert simulation first, scales shares to fit the caps, then executes with the same callback guard. [1](#0-0) 

The callback check at line 165 is the slippage gate for deposits:

```solidity
if (amount0Delta > max0 || amount1Delta > max1) {
    revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
}
```

`removeLiquidity` lives only in the core pool and accepts a `LiquidityDelta` (shares to burn) with no minimum-output parameters: [2](#0-1) 

```solidity
function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
{
    ...
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(...);
    _afterRemoveLiquidity(...);
    // ← no minAmount0 / minAmount1 check
}
```

There is no periphery `removeLiquidity` wrapper in `MetricOmmPoolLiquidityAdder` — the contract only exposes `addLiquidityExactShares` and `addLiquidityWeighted`. [3](#0-2) 

Because bin composition is determined by the oracle price and by every swap that crosses the bin, the amounts an LP receives for a fixed share count can change significantly between the moment they simulate the removal and the moment the transaction executes.

---

### Impact Explanation

An LP who:
1. Reads current bin balances (or uses `simulateSwapAndRevert` / off-chain view) to estimate `amount0Removed` / `amount1Removed` for a given share count,
2. Submits `removeLiquidity` with those shares,

can receive substantially less of one or both tokens if, in the interim, swaps drain the bin of one token (e.g., a large buy of token0 empties `token0BalanceScaled` from the current bin). The LP's shares are burned at the new, depleted composition with no revert path. This is a direct loss of LP principal with no recovery mechanism.

---

### Likelihood Explanation

The Metric OMM pool is oracle-anchored and designed for active trading. Bin composition changes with every swap that crosses the active bin. Any period of market activity between an LP's simulation and their `removeLiquidity` execution — including normal sequencer ordering on an L2 — can shift the composition enough to cause meaningful loss. No privileged actor or malicious setup is required; ordinary market usage triggers the condition.

---

### Recommendation

Add a periphery `removeLiquidity` wrapper (analogous to `addLiquidityExactShares`) that accepts `minAmountToken0` and `minAmountToken1` parameters and reverts if the pool returns less:

```solidity
function removeLiquidityExactShares(
    address pool,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 minAmountToken0,
    uint256 minAmountToken1,
    bytes calldata extensionData
) external returns (uint256 amount0Removed, uint256 amount1Removed) {
    (amount0Removed, amount1Removed) =
        IMetricOmmPoolActions(pool).removeLiquidity(msg.sender, salt, deltas, extensionData);
    if (amount0Removed < minAmountToken0 || amount1Removed < minAmountToken1)
        revert InsufficientOutput(amount0Removed, amount1Removed, minAmountToken0, minAmountToken1);
}
```

Alternatively, add `minAmount0` / `minAmount1` parameters directly to `MetricOmmPool.removeLiquidity` and revert after `LiquidityLib.removeLiquidity` returns.

---

### Proof of Concept

1. LP holds shares in bin 0. They call a view to estimate removal: `amount0 = 1000 USDC`, `amount1 = 0.5 WETH`.
2. Before the LP's transaction lands, a large exact-input swap buys all token0 from bin 0, leaving `token0BalanceScaled = 0`.
3. LP's `removeLiquidity` executes. `LiquidityLib.removeLiquidity` computes their proportional share of the now-depleted bin: `amount0Removed = 0`, `amount1Removed = 0.5 WETH` (or a similarly skewed composition).
4. No revert occurs. The LP receives 0 USDC instead of the expected 1000 USDC, with no recourse. [4](#0-3)

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-81)
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

  /// @notice Add liquidity with explicit per-bin shares for `msg.sender`.
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L165-167)
```text
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
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
