Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates Caller-Supplied `owner` Instead of Actual `sender`, Allowing Full Deposit Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` who calls `addLiquidity` and provides tokens via callback) and instead validates the freely-supplied `owner` parameter against the allowlist. Because `addLiquidity` imposes no constraint that `owner == msg.sender`, any unauthorized address can bypass the deposit guard by naming any already-allowlisted address as `owner`. The pool admin's access-control invariant is completely broken.

## Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from the caller with no constraint that `owner == msg.sender`: [1](#0-0) 

It passes `msg.sender` as `sender` and the caller-supplied `owner` to `_beforeAddLiquidity`: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` encodes both, with `sender` as the first argument: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` then silently drops `sender` (unnamed first parameter) and checks only `owner`: [4](#0-3) 

The token transfer in `LiquidityLib.addLiquidity` is triggered via callback on `msg.sender` (the actual depositor), not on `owner`: [5](#0-4) 

**Exploit path:**
1. `allowedDepositor[pool][alice] = true`; Bob is NOT allowlisted.
2. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `_beforeAddLiquidity(msg.sender=bob, owner=alice, ...)`.
4. Extension checks `allowedDepositor[pool][alice]` → `true` → passes.
5. `LiquidityLib.addLiquidity` mints the position for `alice`, then calls `metricOmmModifyLiquidityCallback` on `msg.sender` (Bob), pulling Bob's tokens.
6. Bob has deposited into the restricted pool; the allowlist guard is bypassed.

The inconsistency is confirmed by `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` and discards `recipient`: [6](#0-5) 

The interface itself names both parameters distinctly, making the intended semantics clear: [7](#0-6) 

## Impact Explanation

A pool admin deploys a pool with `DepositAllowlistExtension` to create a permissioned liquidity pool (e.g., KYC-gated or institution-only). Any unprivileged address can bypass this guard by calling `addLiquidity` with `owner` set to any address already on the allowlist. The unauthorized depositor provides tokens via the modify-liquidity callback and successfully deposits into the restricted pool. The pool admin's access-control invariant is completely broken. This is an admin-boundary break where an unprivileged path bypasses a factory-configured role check.

## Likelihood Explanation

The bypass requires only a single call to `addLiquidity` with a known allowlisted address as `owner`. No special privileges, flash loans, or multi-step setup are needed. The `allowedDepositor` mapping is public, so any observer can identify valid `owner` values. Likelihood is high whenever a pool is deployed with this extension in a non-open (`allowAllDepositors = false`) configuration.

## Recommendation

Replace the `owner` check with a `sender` check, mirroring the pattern used in `SwapAllowlistExtension`:

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

If the intended semantics are to restrict which addresses may *own* positions (rather than which addresses may *deposit*), the contract documentation and the `isAllowedToDeposit`/`setAllowedToDeposit` API naming must be updated to reflect that, and the `sender` path should be separately gated or left open.

## Proof of Concept

```solidity
// Setup: pool deployed with DepositAllowlistExtension
// allowedDepositor[pool][alice] = true
// bob is NOT in the allowlist

// Bob calls addLiquidity with owner = alice
pool.addLiquidity(
    alice,        // owner — allowlisted, passes the guard
    0,            // salt
    deltas,       // liquidity deltas
    callbackData, // bob provides tokens here via metricOmmModifyLiquidityCallback
    extensionData
);

// Result:
// - DepositAllowlistExtension checks allowedDepositor[pool][alice] == true → passes
// - Bob's tokens are transferred into the pool via callback (msg.sender == bob)
// - Position is minted for alice
// - Bob (unauthorized) has successfully deposited into the restricted pool
// - The deposit allowlist guard is bypassed
```

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```
