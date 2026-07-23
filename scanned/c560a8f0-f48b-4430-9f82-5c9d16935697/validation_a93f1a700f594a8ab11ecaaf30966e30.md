### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook silently discards the `sender` parameter (the actual token-providing caller) and checks only the `owner` parameter (the LP position recipient) against the allowlist. Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from any caller with no `msg.sender == owner` requirement, any unprivileged address can bypass the deposit allowlist entirely by supplying an allowlisted address as `owner`.

---

### Finding Description

In `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter — the actual `sender` who calls `addLiquidity` and provides tokens via the swap callback — is unnamed and silently discarded. Only `owner` is checked:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

In `MetricOmmPool.addLiquidity`, the pool passes `msg.sender` as `sender` and the caller-supplied `owner` parameter as `owner` to the extension. Critically, there is **no requirement that `msg.sender == owner`** in `addLiquidity`:

```solidity
function addLiquidity

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
