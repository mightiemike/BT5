### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` parameter (the LP position recipient) against the allowlist, but ignores the `sender` parameter (the actual caller of `addLiquidity`). Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any address not on the allowlist can bypass the gate by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`msg.sender` is the actual caller; `owner` is the user-supplied LP position recipient. There is no check anywhere in `addLiquidity` that `msg.sender == owner`. [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` explicitly discards `sender` (left unnamed) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The allowlist is keyed by `depositor` in the admin setter:

```solidity
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
``` [4](#0-3) 

The name `DepositAllowlistExtension`, the function `setAllowedToDeposit`, and the parameter name `depositor` all signal that the intent is to gate who may **call** `addLiquidity`. The implementation instead gates who may **receive** the LP position — a different address entirely, one that the caller controls freely.

The `ExtensionCalling._beforeAddLiquidity` wrapper confirms that `sender` (first arg) is `msg.sender` of the pool call and `owner` (second arg) is the user-supplied parameter: [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is completely bypassed. Any address not on the allowlist can call `pool.addLiquidity(owner = allowlisted_address, ...)`,

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
