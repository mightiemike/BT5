Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Supplied `owner` Instead of Actual `sender`, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the real transaction initiator) and enforces the allowlist against the caller-supplied `owner` parameter instead. Because `owner` is freely chosen by the caller, any unprivileged address can bypass the deposit allowlist by passing an already-allowlisted address as `owner`, injecting unauthorized liquidity into a permissioned pool.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument and the caller-supplied `owner` as the second argument to `_beforeAddLiquidity`: [1](#0-0) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and discarded; the allowlist check is performed on `owner`: [2](#0-1) 

Since `owner` is a free caller-supplied parameter, any address can pass an allowlisted address as `owner`, satisfy `allowedDepositor[msg.sender][owner]`, and have the deposit accepted. The position is then recorded under `owner` (the allowlisted address) while the actual token transfer is executed by the real caller via the swap callback.

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller), confirming the asymmetry is a defect: [3](#0-2) 

## Impact Explanation
The deposit allowlist is the primary access-control mechanism for restricting who may provide liquidity to a pool. Bypassing it constitutes broken core pool functionality: any address can inject liquidity into a restricted/permissioned pool, altering bin balances, affecting oracle-anchored swap pricing, and potentially triggering or suppressing stop-loss extension watermarks. Pools deployed for permissioned environments (institutional, compliance-gated) have their core access control silently nullified.

## Likelihood Explanation
Exploitation requires no special privilege. Any EOA or contract implementing `IMetricOmmSwapCallback` can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, "")`. The allowlisted address is readable from public on-chain state (`allowedDepositor`). No flash loan, oracle manipulation, or admin cooperation is required.

## Recommendation
Replace the unnamed first parameter with `sender` and enforce the allowlist against it, mirroring `SwapAllowlistExtension`:

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
1. Pool `P` is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Admin calls `setAllowedToDeposit(P, alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) deploys `BobRouter` implementing `IMetricOmmSwapCallback`.
4. `BobRouter` calls `P.addLiquidity(alice, 0, deltas, callbackData, "")`.
5. The extension evaluates `allowedDepositor[P][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` records the position under key `(alice, 0)`.
7. The pool calls `BobRouter.metricOmmSwapCallback(...)`, which transfers the required tokens.
8. Bob has deposited into the restricted pool; the allowlist check was never applied to Bob. [2](#0-1) [4](#0-3)

### Citations

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
