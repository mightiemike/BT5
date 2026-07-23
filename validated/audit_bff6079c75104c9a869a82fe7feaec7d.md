Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on caller-supplied `owner` instead of actual `sender`, allowing allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` who pays tokens) and instead checks only the caller-supplied `owner` argument (the LP-share recipient) against the per-pool allowlist. Because `addLiquidity` imposes no constraint that `msg.sender == owner` (unlike `removeLiquidity`), any unprivileged address can bypass the deposit allowlist by nominating any allowlisted address as `owner`. This renders the pool admin's curation gate entirely inoperative.

## Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` parameter and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both values verbatim and forwards them to every configured extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it unnamed (discarded), then checks only `owner`: [3](#0-2) 

The check `allowedDepositor[msg.sender][owner]` uses `msg.sender` as the pool address (correct) but `owner` as the depositor identity (wrong — `owner` is freely chosen by the caller). There is no guard in `addLiquidity` requiring `msg.sender == owner`; that constraint exists only in `removeLiquidity`: [4](#0-3) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the actual swapper), not on `recipient`: [5](#0-4) 

The root cause is the unnamed first parameter in `beforeAddLiquidity`, which causes the actual caller identity to be silently ignored while a freely-controlled argument is used for the access check.

## Impact Explanation

A pool admin who deploys a curated pool with `DepositAllowlistExtension` intends to restrict liquidity provision to a specific set of addresses. The bypass breaks that invariant: any unprivileged address can add liquidity to the pool by nominating an allowlisted address as `owner`. The non-allowlisted caller pays the tokens via the swap callback; the allowlisted `owner` receives the LP shares; the allowlist check passes. This constitutes both a broken core pool functionality (the curation gate is silently inoperative) and an admin-boundary break (an unprivileged path circumvents the pool admin's access control policy).

## Likelihood Explanation

Exploitation requires only a single public `addLiquidity` call with a crafted `owner` argument. No privileged access, flash loan, or multi-step setup is needed. The `allowedDepositor` mapping is public, so any observer can identify a valid `owner` to supply. The attack is immediately repeatable by any address.

## Recommendation

Replace the unnamed first parameter with `sender` and gate on it instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

This ensures the check covers the actual token-paying caller, not the freely-supplied LP-share recipient.

## Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, Alice, true)`. Bob is not allowlisted.
2. Bob observes `allowedDepositor[pool][Alice] == true` (public mapping).
3. Bob calls `pool.addLiquidity(owner=Alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(msg.sender=Bob, owner=Alice, ...)`.
5. `ExtensionCalling` encodes `(Bob, Alice, ...)` and calls `DepositAllowlistExtension.beforeAddLiquidity`.
6. Extension discards `Bob` (unnamed first param), checks `allowedDepositor[pool][Alice]` → `true` → no revert.
7. `LiquidityLib.addLiquidity` mints LP shares to `Alice`; Bob pays tokens via the callback.
8. Bob has successfully added liquidity to a curated pool without being on the allowlist, violating the pool admin's curation policy.

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
