Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates `owner` Instead of `sender`, Allowing Any Unauthorized Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` discards the `sender` parameter (the actual depositor, i.e., `msg.sender` from `addLiquidity`) and instead validates `owner` (the caller-supplied LP position recipient). Because `addLiquidity` imposes no constraint that `msg.sender == owner`, any unauthorized address can bypass the allowlist by specifying any allowlisted address as `owner`, rendering the guard completely ineffective.

## Finding Description
`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." The `beforeAddLiquidity` hook receives `sender` (the address that called `addLiquidity`, bound to `msg.sender` in the pool) and `owner` (the caller-supplied LP position recipient). The hook silently discards `sender` and checks only `owner`:

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
``` [1](#0-0) 

The pool's `addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner`, with no constraint that `msg.sender == owner`: [2](#0-1) 

By contrast, `removeLiquidity` enforces `msg.sender == owner` before calling the hook: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` correctly checks `sender`, not the recipient: [4](#0-3) 

**Exploit path:**
1. Unauthorized address `X` reads `allowedDepositor[pool][A]` on-chain to find any allowlisted address `A`.
2. `X` calls `pool.addLiquidity(A, salt, deltas, callbackData, extensionData)`.
3. The pool calls `_beforeAddLiquidity(msg.sender=X, owner=A, ...)`.
4. The extension checks `allowedDepositor[pool][A]` → `true`. Guard passes.
5. The pool calls `IMetricOmmAddLiquidityCallback(X).metricOmmAddLiquidityCallback(...)` — `X` provides tokens.
6. LP shares are credited to `A`'s position. `X` has deposited into a permissioned pool without authorization.

The wrong value is `allowedDepositor[pool][owner]` — the check should be `allowedDepositor[pool][sender]`.

## Impact Explanation
The deposit allowlist guard is completely ineffective. Any unauthorized address can deposit into a pool configured with `DepositAllowlistExtension` by supplying any allowlisted address as `owner`. This defeats the pool admin's intent to restrict liquidity providers, allows unauthorized liquidity to enter the pool (diluting or manipulating existing LP positions and bin distributions), and violates any compliance or access-control requirement the pool admin intended to enforce. This constitutes a broken core pool access-control mechanism with direct fund impact on existing LPs.

## Likelihood Explanation
The bypass requires only a standard `addLiquidity` call. The allowlisted `owner` address is publicly readable from the `allowedDepositor` mapping. No special privileges, flash loans, oracle manipulation, or privileged setup are needed. Any address can execute this at any time against any pool using this extension.

## Recommendation
Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

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

Also update `setAllowedToDeposit` and `isAllowedToDeposit` documentation to clarify that the allowlist governs the depositor (caller of `addLiquidity`), not the position owner.

## Proof of Concept

```solidity
// Setup: pool has DepositAllowlistExtension configured.
// allowedDepositor[pool][alice] = true
// bob is NOT on the allowlist.

// Bob calls addLiquidity specifying alice as owner.
// The extension checks allowedDepositor[pool][alice] → true → passes.
// Bob provides tokens via metricOmmAddLiquidityCallback.
// Alice receives LP shares.
// Bob has successfully deposited into a permissioned pool without being on the allowlist.

pool.addLiquidity(
    alice,       // owner — allowlisted, passes the guard
    0,           // salt
    deltas,
    callbackData,
    extensionData
);
// Bob's tokens are now in the pool. Guard bypassed.
```

A Foundry integration test can confirm this by: (1) deploying a pool with `DepositAllowlistExtension`, (2) allowlisting `alice` but not `bob`, (3) calling `pool.addLiquidity(alice, ...)` from `bob`'s address, and (4) asserting the call succeeds and `bob`'s tokens enter the pool.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L204-207)
```text
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
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
