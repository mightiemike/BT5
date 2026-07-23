### Title
`DepositAllowlistExtension.beforeAddLiquidity` Guards on Position `owner` Instead of Actual Depositor `sender`, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently ignores the actual caller (`sender`) and instead checks the position `owner`. Because `MetricOmmPoolLiquidityAdder` lets any caller specify an arbitrary `owner`, any unprivileged actor can bypass the deposit allowlist by naming an allowlisted address as the position owner while paying the tokens themselves.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two actor addresses: the first (unnamed, discarded) is the actual caller of `pool.addLiquidity()`, and the second is `owner` — the position recipient. The guard is applied only to `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
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
``` [1](#0-0) 

The pool's `addLiquidity` passes `msg.sender` (the actual depositor) as the first argument and `owner` as the second:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the explicit-owner overload) accepts any non-zero `owner` and uses `msg.sender` as the payer:

```solidity

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```
