Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates Position Recipient Instead of Token Payer, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and checks only `owner` (the caller-supplied position recipient). Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any unprivileged address can bypass the deposit guard by supplying any allowlisted address as `owner`, causing unauthorized funds to enter a pool explicitly configured to be restricted.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as two distinct arguments to the hook: [1](#0-0) 

There is no guard requiring `msg.sender == owner` in `addLiquidity`, unlike `removeLiquidity` which enforces it explicitly: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the first parameter (`sender`) and validates only `owner`: [3](#0-2) 

Inside the extension, `msg.sender` is the pool, so the effective check is `allowedDepositor[pool][owner]` — the position recipient — never the token-paying caller. By contrast, `SwapAllowlistExtension.beforeSwap` correctly validates `sender`: [4](#0-3) 

The asymmetry is the root cause: the deposit hook uses the wrong identity field.

## Impact Explanation

A pool configured with `DepositAllowlistExtension` for KYC, regulatory compliance, or LP whitelisting receives no protection. Any address — including sanctioned or explicitly blocked addresses — can deposit tokens into the restricted pool. The allowlist admin-boundary control is completely inoperative for deposits, constituting a broken core pool functionality and an admin-boundary break where an unprivileged caller bypasses an explicitly configured access restriction, causing unauthorized funds to enter the pool.

## Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with any known allowlisted address as `owner`. No special privileges, flash loans, or multi-step setup are needed. Allowlisted addresses are publicly discoverable via the `allowedDepositor` mapping or emitted `AllowedToDepositSet` events. The attack is trivially repeatable by any address.

## Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][Alice] = true
  Bob is NOT allowlisted

Attack:
  Bob calls pool.addLiquidity(
      owner        = Alice,   // allowlisted address
      salt         = 0,
      deltas       = <valid liquidity delta>,
      callbackData = <Bob pays tokens in callback>,
      extensionData = ""
  )

Extension check (DepositAllowlistExtension.beforeAddLiquidity):
  msg.sender = pool
  owner      = Alice
  allowedDepositor[pool][Alice] == true  →  hook returns selector (no revert)

Result:
  Bob's tokens enter the restricted pool via the swap callback.
  Position (owner=Alice, salt=0) is minted.
  Bob's funds are in the pool; the deposit allowlist is fully bypassed.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
