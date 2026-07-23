### Title
`DepositAllowlistExtension` Checks LP Position `owner` Instead of Actual Depositor `sender`, Allowing Unauthorized Liquidity Addition — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook silently ignores the `sender` parameter (the actual depositor who provides tokens via callback) and instead checks the `owner` parameter (the LP position recipient). Any unprivileged address can bypass the deposit allowlist by calling `addLiquidity` with an allowlisted `owner` address, injecting unauthorized liquidity into a permissioned pool.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts two distinct addresses: `msg.sender` (the caller who will satisfy the token callback) and `owner` (the address that receives the LP position). These are forwarded to the extension as `sender` and `owner` respectively: [1](#0-0) [2](#0-1) 

The extension receives both values, but its implementation discards `sender` (written as the anonymous `address,`) and gates only on `owner`:

<cite repo="Oyahkilomeikhide/2026-07-metric-dev-oyakhil-main--

### Citations

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
