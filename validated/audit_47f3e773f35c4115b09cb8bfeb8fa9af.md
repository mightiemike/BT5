Audit Report

## Title
`addLiquidity` Bypasses `whenNotPaused` Guard, Allowing Deposits During Emergency Pause — (File: metric-core/contracts/MetricOmmPool.sol)

## Summary

`MetricOmmPool.addLiquidity` accepts new LP deposits even when `pauseLevel` is non-zero, because it carries only `nonReentrant` and no `whenNotPaused` modifier. `swap` is correctly gated by `whenNotPaused`, but the asymmetry means a pool paused for an oracle anomaly or price manipulation still accepts new capital. When the pool is unpaused, all liquidity deposited during the pause window is immediately live and tradeable, exposing depositors to bad-price swaps at the moment of unpause.

## Finding Description

`swap` is declared with `whenNotPaused`: [1](#0-0) 

`_checkNotPaused` reverts for any non-zero `pauseLevel` (admin pause = 1, protocol pause = 2): [2](#0-1) 

`addLiquidity`, however, carries only `nonReentrant` — no pause check: [3](#0-2) 

The extension hooks `_beforeAddLiquidity` / `_afterAddLiquidity` are invoked but contain no pause-awareness — they only dispatch to configured extension contracts: [4](#0-3) 

At unpause, `_getBidAndAskPriceX64` fetches the live oracle price, which may still be recovering from the anomaly that triggered the pause: [5](#0-4) 

## Impact Explanation

Any LP who deposits during a pause window has their tokens immediately exposed to swaps the instant `pauseLevel` returns to 0. If the pause was triggered by oracle manipulation or a bad price feed, an attacker can call `swap` at the still-recovering oracle price immediately after unpause, trading against the freshly deposited liquidity at an unfavorable rate. This constitutes a direct loss of user principal — a High/Medium impact under Sherlock thresholds, as it drains real token balances from LP positions through a bad-price swap that the pause mechanism was specifically designed to prevent.

## Likelihood Explanation

Medium. Pool pauses are infrequent but are specifically designed for emergency scenarios — exactly the conditions where depositing into a paused pool is most dangerous. An automated LP manager or a user who observes that `swap` reverts with `PoolPaused` may incorrectly infer the pool is safe to deposit into, since `addLiquidity` succeeds without error. The attacker only needs to monitor the mempool for the unpause transaction and front-run or immediately follow it with a `swap`.

## Recommendation

Add `whenNotPaused` to `addLiquidity`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added)
```

Alternatively, introduce a granular pause flag (e.g., `pauseDeposits`) so that `removeLiquidity` remains unguarded (LPs can always exit) while `addLiquidity` is blocked during emergency pauses.

## Proof of Concept

1. Oracle reports a manipulated price; admin calls `factory.setPause(pool, 1)` → `pauseLevel = 1`.
2. `swap` reverts with `PoolPaused`. Swaps are blocked.
3. Alice calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)` — **succeeds** because `addLiquidity` has no `whenNotPaused` guard. [6](#0-5) 
4. Admin resolves the oracle issue and calls `factory.setPause(pool, 0)` → `pauseLevel = 0`.
5. Attacker immediately calls `swap` at the still-recovering oracle price, trading against Alice's freshly deposited liquidity. [5](#0-4) 
6. Alice's deposited tokens are partially drained; attacker profits at Alice's expense.

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```
