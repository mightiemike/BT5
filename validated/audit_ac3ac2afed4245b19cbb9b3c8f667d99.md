Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of the pool call) and gates on `owner` instead — a free parameter chosen by the caller. Because `owner` is caller-supplied in `MetricOmmPool.addLiquidity`, any unprivileged address can bypass the pool admin's deposit allowlist by nominating any already-authorized address as `owner`. This completely defeats the admin-configured access control without requiring any privileged action.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument to the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(address sender, address owner, ...)` but leaves `sender` unnamed and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [2](#0-1) 

Since `owner` is a free parameter in `addLiquidity`, any caller can pass any address as `owner`. If that address is in `allowedDepositor[pool]`, the check passes regardless of who the actual caller (`sender`) is. The allowlist mapping is keyed by depositor address:

```solidity
mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
``` [3](#0-2) 

The analogous `SwapAllowlistExtension.beforeSwap` correctly gates on `sender`:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

The inconsistency confirms the deposit check is on the wrong parameter.

## Impact Explanation
This is an admin-boundary break: the pool admin's deposit allowlist — a pool-admin security control — is bypassed by any unprivileged caller. An unauthorized address Bob can call `pool.addLiquidity(owner = Alice, ...)` where Alice is allowlisted. The extension evaluates `allowedDepositor[pool][Alice] → true` and does not revert. Bob supplies tokens through the liquidity callback and Alice receives the position. The pool admin's intent to restrict which addresses may deposit capital is completely defeated.

## Likelihood Explanation
The bypass requires only a single public call to `addLiquidity` with a known authorized address as `owner`. The `allowedDepositor` mapping is public, so any observer can identify valid authorized addresses. No special permissions, flash loans, or oracle manipulation are needed. The attack is immediately executable by any address.

## Recommendation
Replace the unnamed first parameter with `sender` and gate on it instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This aligns with the NatSpec ("Gates `addLiquidity` by depositor address") and mirrors the correct pattern in `SwapAllowlistExtension`.

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension`; `allowedDepositor[pool][Alice] = true`; Bob is not on the allowlist.
2. Bob calls `pool.addLiquidity(owner = Alice, salt = 0, deltas, callbackData, extensionData)`.
3. `beforeAddLiquidity` is invoked with `sender = Bob` (discarded), `owner = Alice`. The check evaluates `allowedDepositor[pool][Alice] → true`. No revert.
4. Bob's liquidity callback transfers tokens; Alice receives the position.
5. The deposit allowlist is bypassed without any privileged action.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-13)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-40)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
