Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unprivileged Caller to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` validates the LP position `owner` parameter against the allowlist instead of the actual transaction initiator (`sender`). Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from any caller without requiring `msg.sender == owner`, any non-allowlisted address can bypass the deposit guard by naming any allowlisted address as `owner`. The pool admin's intent to restrict depositors is fully defeated.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`, with no requirement that `msg.sender == owner`: [1](#0-0) 

Unlike `removeLiquidity`, which enforces `msg.sender == owner`: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` correctly forwards both `sender` and `owner` to the extension: [3](#0-2) 

However, `DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first unnamed parameter) and checks only `owner` against the allowlist: [4](#0-3) 

The allowlist mappings are keyed by `depositor` address: [5](#0-4) 

Because `owner` is freely chosen by the caller, any non-allowlisted address can pass the guard by supplying any allowlisted address as `owner`. The actual depositor (the address that provides tokens via the swap callback) is never checked.

## Impact Explanation
The `DepositAllowlistExtension` is rendered completely ineffective as an access-control mechanism. Any unprivileged caller can deposit tokens into a pool configured with this extension by setting `owner` to any allowlisted address. This is a direct admin-boundary break: a pool-admin-configured guard is bypassed by an unprivileged path. Pools relying on this extension for KYC compliance, whitelist-only liquidity, or deposit-cap enforcement receive no protection.

## Likelihood Explanation
The bypass requires only a single `addLiquidity` call with a known allowlisted address as `owner`. No special privileges, flash loans, or complex setup are needed. Allowlisted addresses are publicly discoverable via the `AllowedToDepositSet` event or by reading `allowedDepositor`. Likelihood is high.

## Recommendation
Change the allowlist check in `beforeAddLiquidity` to validate `sender` (the actual depositor/caller) rather than `owner` (the LP position recipient):

```solidity
// After (correct):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept
1. Pool `P` is deployed with `DepositAllowlistExtension` as its `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(P, Alice, true)`. Bob is **not** allowlisted.
3. Bob calls `P.addLiquidity(owner=Alice, salt=0, deltas=..., callbackData=..., extensionData=...)`.
4. The pool calls `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. The extension evaluates `allowedDepositor[P][Alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` credits LP shares to `Alice`.
7. The pool calls `IMetricOmmSwapCallback(Bob).metricOmmSwapCallback(...)` — Bob's contract transfers the required tokens.
8. Bob has successfully deposited into the restricted pool. The allowlist guard was never applied to the actual depositor.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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
