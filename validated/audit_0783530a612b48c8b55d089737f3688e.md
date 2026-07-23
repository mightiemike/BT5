### Title
Missing Slippage Protection in `removeLiquidity` — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.removeLiquidity` accepts no minimum-output parameters. The token amounts returned depend on the live bin composition at execution time, which is determined by the oracle price and cursor position (`curBinIdx`, `curPosInBin`). Swaps between transaction submission and execution can shift the cursor and change bin composition, causing an LP to receive materially less value than expected with no on-chain protection.

---

### Finding Description

`removeLiquidity` is defined as:

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
) external nonReentrant(PoolActions.REMOVE_LIQUIDITY)
  returns (uint256 amount0Removed, uint256 amount1Removed)
``` [1](#0-0) 

The function accepts shares to burn but has no `minAmount0Out` / `minAmount1Out` parameters. The amounts returned are computed from the live bin state (`_binStates`, `binTotals`, `curBinIdx`, `curPosInBin`) at the moment of execution. [2](#0-1) 

The `msg.sender != owner` guard forces the position owner to call the pool directly — there is no periphery wrapper for `removeLiquidity`. By contrast, `addLiquidity` has `MetricOmmPoolLiquidityAdder`, which enforces `maxAmountToken0` / `maxAmountToken1` caps in the callback:

```solidity
if (amount0Delta > max0 || amount1Delta > max1) {
    revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
}
``` [3](#0-2) 

No equivalent protection exists for the remove path. The `addLiquidityWeighted` function also has `_validateBinAndBinPosition` to bound cursor state at deposit time: [4](#0-3) 

No analogous cursor-bound check exists for `removeLiquidity`.

The bin composition (ratio of `token0BalanceScaled` to `token1BalanceScaled`) changes continuously as swaps move the cursor through bins. The oracle-derived `bidPriceX64` / `askPriceX64` used in swap math is fetched fresh on every swap: [5](#0-4) 

A sandwich attacker can therefore manipulate the cursor position before the victim's `removeLiquidity` executes, causing the victim to receive a worse token mix.

---

### Impact Explanation

**Medium — direct loss of LP principal above dust thresholds.**

An LP removing a position from a bin that currently holds both tokens can be sandwiched so that the bin is drained of the more valuable token before their transaction executes. They receive the residual (less valuable) token instead. The loss is proportional to position size and the attacker's ability to move the cursor, which is bounded only by available liquidity in adjacent bins. There is no on-chain guard that reverts the transaction when the output falls below the LP's expectation.

---

### Likelihood Explanation

**Medium.** Every `removeLiquidity` call is visible in the mempool. MEV bots routinely sandwich LP withdrawals. The `msg.sender == owner` constraint does not prevent sandwiching — it only prevents a third party from initiating the removal. The attack requires no special permissions and works against any LP whose position spans a bin with mixed token composition.

---

### Recommendation

Add `minAmount0Out` and `minAmount1Out` parameters to `removeLiquidity` and revert after the amounts are computed if either falls below the caller's minimum:

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 minAmount0Out,   // <-- add
    uint256 minAmount1Out,   // <-- add
    bytes calldata extensionData
) external nonReentrant(PoolActions.REMOVE_LIQUIDITY)
  returns (uint256 amount0Removed, uint256 amount1Removed)
{
    ...
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(...);
    if (amount0Removed < minAmount0Out || amount1Removed < minAmount1Out)
        revert InsufficientOutput(amount0Removed, amount1Removed, minAmount0Out, minAmount1Out);
    ...
}
```

Alternatively, provide a periphery wrapper (analogous to `MetricOmmPoolLiquidityAdder`) that enforces minimum-output checks before forwarding the call — though the `msg.sender == owner` constraint means the pool itself must be updated.

---

### Proof of Concept

1. LP holds shares in bin 0, which currently contains 50 % token0 / 50 % token1. At current oracle price, the LP expects to receive roughly equal value of both tokens.
2. Attacker observes the LP's `removeLiquidity` transaction in the mempool.
3. Attacker front-runs with a large `swap` (token1 → token0), moving the cursor deep into bin 0. After the swap, bin 0 holds mostly token1 (token0 has been bought out). The oracle price used is fetched live via `_getBidAndAskPriceX64()`. [6](#0-5) 
4. LP's `removeLiquidity` executes. `LiquidityLib.removeLiquidity` computes amounts from the now-manipulated bin state. The LP receives mostly token1 (the less valuable side after the attacker's swap) and very little token0.
5. Attacker back-runs with the reverse swap (token0 → token1), restoring the cursor and profiting from the round-trip spread.

Because `removeLiquidity` returns `amount0Removed` and `amount1Removed` with no floor check, the transaction succeeds regardless of how unfavorable the output is. [1](#0-0)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-228)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L104-104)
```text
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L165-167)
```text
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```
