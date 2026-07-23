### Title
`addLiquidity` Lacks `whenNotPaused` Guard, Allowing LP Deposits Into a Paused Pool — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.swap` is protected by the `whenNotPaused` modifier, but `MetricOmmPool.addLiquidity` is not. When the pool is paused (e.g., due to an oracle failure or price manipulation incident), swaps are correctly blocked, but LPs can still deposit principal into the compromised pool. If the pool is later unpaused with a bad or stale oracle price, the newly deposited LP funds are immediately exposed to arbitrage loss.

---

### Finding Description

`MetricOmmPool` has a two-level pause mechanism controlled by the factory: [1](#0-0) [2](#0-1) [3](#0-2) 

`swap` correctly applies `whenNotPaused`: [4](#0-3) 

`addLiquidity` does **not** apply `whenNotPaused`: [5](#0-4) 

The pool is oracle-anchored: every swap prices tokens against a live `IPriceProvider` bid/ask. A pause is the primary emergency response to a bad oracle (stale feed, inverted prices, manipulation). While the pause correctly blocks swaps, it leaves `addLiquidity` open. An LP who deposits during the pause will have their shares priced against the pool's current bin state, which was set at the last valid oracle snapshot before the pause. When the pool is unpaused and the oracle resumes (possibly at a very different price), the LP's position is immediately mispriced and arbitrageable.

The `MetricOmmPoolLiquidityAdder` periphery contract also routes directly to `pool.addLiquidity`, so the bypass is reachable through both the direct pool call and the supported periphery path: [6](#0-5) 

---

### Impact Explanation

When the pool is paused due to an oracle incident:
- Swaps are blocked, preventing further price-sensitive settlement.
- `addLiquidity` remains open, allowing LPs to deposit principal at the stale/pre-pause bin cursor.
- On unpause, the oracle resumes at a potentially very different price. The LP's shares are immediately worth less than deposited because the bin cursor and token ratios no longer match the live oracle price.
- The loss is direct LP principal loss, not a theoretical edge case — it is the same mechanism that makes pausing necessary in the first place.

This matches the Metric OMM Allowed Impact Gate: **direct loss of user principal** and **broken core pool functionality causing loss of funds**.

---

### Likelihood Explanation

- Requires the pool to be paused (factory admin action, level 1 or 2). This is a semi-trusted trigger, but the pause is specifically designed for emergency use, making it a realistic scenario.
- Once paused, any LP (unprivileged) can call `addLiquidity` directly or via `MetricOmmPoolLiquidityAdder` — no special access required.
- The LP may not know why the pool is paused or that depositing during a pause is unsafe.
- The `DepositAllowlistExtension` (if configured) only gates by depositor identity, not by pause state, so it provides no protection here. [7](#0-6) 

---

### Recommendation

Add the `whenNotPaused` modifier to `addLiquidity`, mirroring the protection already applied to `swap`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

`removeLiquidity` should remain unguarded (or explicitly allowed when paused) so existing LPs can exit a paused pool — this is the correct asymmetry. Only new deposits should be blocked.

---

### Proof of Concept

1. Pool is deployed with an oracle price provider. Pool admin configures `PriceVelocityGuardExtension` or `OracleValueStopLossExtension` as a safety measure.
2. Oracle feed becomes stale or manipulated. Factory admin calls `setPause(1)` to pause the pool at level 1.
3. `swap` now reverts with `PoolPaused`. Swaps are blocked.
4. LP calls `pool.addLiquidity(owner, salt, deltas, callbackData, extensionData)` directly (or via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares`). The call succeeds — no `whenNotPaused` check exists on this path.
5. LP's tokens are transferred into the pool and shares are minted at the current (stale) bin cursor.
6. Factory admin resolves the oracle issue and calls `setPause(0)` to unpause.
7. The live oracle price has moved significantly. Arbitrageurs immediately swap against the LP's newly deposited position at the stale bin price, extracting value from the LP's principal. [5](#0-4) [4](#0-3) [8](#0-7)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
