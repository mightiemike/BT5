Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Supplied `owner` Instead of Actual Depositor `sender`, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead checks `owner`, a free caller-supplied argument that designates the LP-share recipient. Because any caller can supply an already-authorized address as `owner`, the allowlist check always passes regardless of who is actually depositing. The pool admin's access-control boundary is bypassed by any unprivileged address.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter (`sender`) as unnamed, discarding it entirely, and only checks `owner`: [2](#0-1) 

The check `allowedDepositor[msg.sender][owner]` therefore validates the LP-share recipient, not the depositing address. Since `owner` is a free argument in the public `addLiquidity` interface, any caller can pass an authorized address as `owner` to satisfy the check.

Contrast with `SwapAllowlistExtension.beforeSwap`, which correctly names and checks `sender`: [3](#0-2) 

`removeLiquidity` enforces `msg.sender == owner`, so the attacker cannot reclaim the LP shares minted to `owner`: [4](#0-3) 

The attacker's tokens are pulled via the swap callback and permanently enter the pool, while LP shares are minted to the authorized address the attacker named.

## Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may add liquidity (regulatory compliance, curated LP sets, controlled bootstrapping). With this bug, any unprivileged address can inject capital into a restricted pool by naming an authorized address as `owner`. The allowlist invariant is permanently violated: unauthorized capital enters the pool, distorting bin balances and undermining any compliance or economic rationale behind the allowlist. This matches the "Admin-boundary break" impact criterion: factory/oracle role checks are bypassed by an unprivileged path.

## Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a known authorized address as `owner`. The `allowedDepositor` mapping is public, so any observer can identify authorized addresses. No special privileges, flash loans, or oracle manipulation are needed. The attack is repeatable by any address at any time the pool has the extension configured. [5](#0-4) 

## Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP recipient), mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the depositor and the owner, both should be checked independently.

## Proof of Concept

```
Setup:
  Pool P has DepositAllowlistExtension configured.
  allowedDepositor[P][AUTHORIZED] = true
  allowedDepositor[P][ATTACKER]   = false (not set)

Attack:
  ATTACKER calls pool.addLiquidity(
      owner        = AUTHORIZED,   // passes the allowlist check
      salt         = 0,
      deltas       = <valid delta>,
      callbackData = ...,
      extensionData = ""
  )

Extension check (beforeAddLiquidity):
  msg.sender = P (the pool)
  owner      = AUTHORIZED
  allowedDepositor[P][AUTHORIZED] == true  → check passes

Result:
  - ATTACKER's tokens are pulled via metricOmmSwapCallback
  - LP shares are minted to AUTHORIZED
  - ATTACKER has deposited into a pool that explicitly excluded them
  - The allowlist invariant is violated; unauthorized capital is in the pool
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
