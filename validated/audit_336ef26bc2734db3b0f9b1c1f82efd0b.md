### Title
`addLiquidity` Bypasses `whenNotPaused` Guard, Allowing Deposits During Emergency Pause — (File: metric-core/contracts/MetricOmmPool.sol)

---

### Summary

`MetricOmmPool.swap` is protected by `whenNotPaused`, but `addLiquidity` carries no equivalent guard. When the pool is paused for an emergency (oracle anomaly, price manipulation), swaps are blocked yet new LP deposits are freely accepted. The moment the pool is unpaused, every position added during the pause is immediately exposed to swaps — the direct analog of the sDaiStrategy bug where unpausing for withdrawals also re-enables deposits.

---

### Finding Description

`swap` is declared with `whenNotPaused`: [1](#0-0) 

`_checkNotPaused` reverts on any non-zero `pauseLevel`: [2](#0-1) 

`addLiquidity`, however, carries only `nonReentrant` — no pause check: [3](#0-2) 

`removeLiquidity` is similarly unguarded, which is intentional (LPs must always be able to exit). But `addLiquidity` being unguarded is not symmetric: it allows new capital to enter a pool that the admin has deliberately frozen.

The extension hooks `_beforeAddLiquidity` / `_afterAddLiquidity` are still invoked: [4](#0-3) 

But those hooks are per-pool configured guards (e.g., `DepositAllowlistExtension`) and contain no pause-awareness. They do not substitute for the missing `whenNotPaused` check.

`pauseLevel` supports two non-zero states (admin pause = 1, protocol pause = 2): [5](#0-4) 

Both are intended to halt pool activity, yet only `swap` is halted.

---

### Impact Explanation

When the pool is unpaused, all liquidity added during the pause window is immediately live and tradeable. If the pause was triggered by an oracle anomaly or price manipulation, an attacker can call `swap` the instant `pauseLevel` returns to 0, trading against the newly deposited LP positions at the still-recovering or briefly-bad oracle price. This constitutes a direct loss of user principal for any LP who deposited during the pause. The pool's own `_getBidAndAskPriceX64` will use whatever the oracle reports at unpausing time: [6](#0-5) 

---

### Likelihood Explanation

Medium. Pool pauses are infrequent but are specifically designed for emergency scenarios — exactly the conditions where depositing into a paused pool is most dangerous. An automated LP manager or a user who observes that `swap` is reverting with `PoolPaused` may incorrectly infer the pool is safe to deposit into, since `addLiquidity` succeeds without error.

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
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) ...
```

Alternatively, introduce a granular pause flag (e.g., `pauseDeposits`) so that `removeLiquidity` remains unguarded (LPs can always exit) while `addLiquidity` is blocked during emergency pauses — matching the recommendation in the external report.

---

### Proof of Concept

1. Oracle reports a manipulated price; admin calls `factory.setPause(pool, 1)` → `pauseLevel = 1`.
2. `swap` reverts with `PoolPaused`. Swaps are blocked.
3. Alice calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)` — **succeeds** because `addLiquidity` has no `whenNotPaused` guard. Alice's tokens enter the paused pool.
4. Admin resolves the oracle issue and calls `factory.setPause(pool, 0)` → `pauseLevel = 0`.
5. Attacker front-runs the unpause (or acts immediately after) and calls `swap` at the (still-recovering) oracle price, trading against Alice's freshly deposited liquidity.
6. Alice's deposited tokens are partially drained; attacker profits at Alice's expense.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L71-72)
```text
  /// @dev 0 = active, 1 = paused by admin, 2 = paused by protocol. Transitions enforced by factory.
  uint8 internal pauseLevel;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
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
