### Title
Paused pool still accepts `addLiquidity`, exposing deposited principal to loss on unpause — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool` applies the `whenNotPaused` guard exclusively to `swap`, leaving `addLiquidity` (and `removeLiquidity`) unguarded. This is the direct Metric OMM analog of the Primitive Protocol bug: a lifecycle guard is wired to some operations but silently absent from others, allowing fund-impacting actions to proceed in a state the protocol considers unsafe.

---

### Finding Description

`MetricOmmPool` declares a `pauseLevel` state variable (0 = active, 1 = admin-paused, 2 = protocol-paused) and a `whenNotPaused` modifier that reverts when `pauseLevel != 0`. [1](#0-0) [2](#0-1) [3](#0-2) 

The modifier is applied to `swap`: [4](#0-3) 

But `addLiquidity` carries only `nonReentrant` — no pause check: [5](#0-4) 

And `removeLiquidity` is identical in this respect: [6](#0-5) 

The extension hooks `_beforeAddLiquidity` / `_afterAddLiquidity` are still invoked inside `addLiquidity`, so any extension guard (e.g., `DepositAllowlistExtension`) runs — but the fundamental pool-level pause invariant is never enforced. An LP depositing into a paused pool bypasses the safety boundary the pause was meant to create. [7](#0-6) 

---

### Impact Explanation

The pause mechanism exists to protect users from trading in a compromised state (e.g., oracle manipulation, price feed failure). When the pool is paused:

1. Swaps are blocked — the oracle-derived bid/ask cannot be acted upon.
2. But `addLiquidity` succeeds — an LP deposits real tokens into bins priced by the same compromised oracle state that triggered the pause.
3. When the pool is unpaused, those bins are immediately exposed to swaps. An attacker who knows the oracle was manipulated can drain the newly deposited liquidity at the stale/manipulated price before the oracle recovers.

This is a **direct loss of LP principal** with no recourse: the LP's funds entered the pool in a state the protocol itself declared unsafe, and the pool's own accounting (`binTotals`, `_binStates`) reflects the deposit at the compromised price.

---

### Likelihood Explanation

- Pausing is a real operational event (admin or protocol can trigger it via `setPause`).
- LPs interacting through a router or UI may not inspect `pauseLevel` before calling `addLiquidity`.
- An attacker who detects an imminent pause (e.g., by watching the mempool for `setPause`) can front-run with a large `addLiquidity` call, then back-run the unpause with a swap that extracts value from the newly deposited liquidity.
- No special privilege is required for the attacker — only a standard `addLiquidity` call. [8](#0-7) 

---

### Recommendation

Add `whenNotPaused` to `addLiquidity`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
```

`removeLiquidity` should remain unguarded (or guarded only at level 2) so existing LPs can always exit — matching the Primitive Protocol lesson that token returns to the contract must remain possible even in a restricted state.

---

### Proof of Concept

```
1. Pool is live; oracle reports fair bid/ask.
2. Oracle begins reporting a manipulated price (e.g., bid/ask shifted 10%).
3. Protocol detects anomaly; calls factory → pool.setPause(1).
   - swap() now reverts with PoolPaused.
   - addLiquidity() has NO such check — it succeeds.
4. Attacker (or unsuspecting LP) calls addLiquidity() with a large token0/token1 deposit.
   - Tokens enter bins priced at the manipulated oracle mid-price.
   - _beforeAddLiquidity extension hooks run normally; no pause revert.
5. Protocol unpauses: pool.setPause(0).
6. Attacker immediately calls swap() — zeroForOne — buying token0 out of the
   newly deposited bins at the manipulated (below-market) ask price.
7. LP's deposited token0 is drained; attacker profits the price-manipulation spread.
   LP has no recourse: the pool's accounting correctly reflects the swap.
``` [9](#0-8) [10](#0-9)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L247-278)
```text
    (int256 amount0Delta, int256 amount1Delta, uint256 protocolFeeAmount) =
      _executeSwap(zeroForOne, amountSpecified, params);

    if (zeroForOne) {
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
    } else {
      if (amount0Delta < 0) {
        // casting to uint256 is safe because amount0Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken0(recipient, uint256(-amount0Delta));
      }

      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
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
