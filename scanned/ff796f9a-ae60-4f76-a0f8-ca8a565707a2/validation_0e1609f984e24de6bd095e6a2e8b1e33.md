### Title
`DepositAllowlistExtension` gates `owner` (position recipient) instead of `sender` (actual depositor), allowing any caller to bypass the deposit allowlist via `MetricOmmPoolLiquidityAdder` — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook enforces its guard against the **position owner** (`owner`) rather than the **actual depositor** (`sender`). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lets any caller freely specify an arbitrary `owner`, a non-allowlisted user can bypass the deposit guard entirely by naming any allowlisted address as the position recipient.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is the hook that enforces the deposit allowlist:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first parameter — `sender`, the address that called `pool.addLiquidity()` — is silently discarded (unnamed). The guard checks only `owner`, the position recipient. [1](#0-0) 

Compare this with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper) and ignores the recipient:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

The asymmetry is the root cause: `SwapAllowlistExtension` gates the actor who pays/initiates; `DepositAllowlistExtension` gates the actor who receives shares.

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the `owner`-specifying overload) accepts any non-zero `owner` address — the only validation is `_validateOwner(owner)` which only checks `owner != address(0)`. The actual payer is `msg.sender`, stored separately in transient storage:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(
    address pool,
    address owner,       // ← caller-controlled, any non-zero address
    uint80 salt,
    LiquidityDelta calldata deltas,
    ...
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);   // only checks owner != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
    //                                              ^^^^^^^^^^
    //                         actual payer stored in transient storage
}
``` [3](#0-2) 

The pool then calls `_beforeAddLiquidity(sender=LiquidityAdder, owner=attacker-chosen-address, ...)`. The extension sees `owner` = the allowlisted address and passes.

<cite repo="patrichyt/2026-07-metric-dev-oyakhil-main--020" path="metric-core

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
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
