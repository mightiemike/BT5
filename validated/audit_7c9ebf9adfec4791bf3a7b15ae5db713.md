Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates LP Recipient (`owner`) Instead of Token Provider (`sender`), Allowing Any Caller to Bypass the Deposit Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address, but its `beforeAddLiquidity` hook validates the `owner` argument (the LP position recipient, freely chosen by the caller) rather than the `sender` argument (the actual `msg.sender` who provides tokens via callback). Because `owner` is caller-supplied and `allowedDepositor` is publicly readable, any unprivileged caller can bypass the allowlist by naming any already-authorized address as `owner`, injecting liquidity into a gated pool and permanently altering its bin composition and marginal prices.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` into the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The extension hook receives both but silently discards the first parameter (`sender`) and checks only `owner`:

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
```

After the hook passes, `LiquidityLib.addLiquidity` calls back on `msg.sender` of the pool call (the actual caller, not `owner`) to collect tokens:

```solidity
// metric-core/contracts/libraries/LiquidityLib.sol L147-148
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```

**Exploit path:**
1. `bob` (not on allowlist) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")` where `alice` is on the allowlist.
2. Pool calls `_beforeAddLiquidity(bob, alice, ...)`.
3. Extension evaluates `allowedDepositor[pool][alice] == true` → check passes.
4. Pool calls `bob.metricOmmModifyLiquidityCallback(...)` → bob supplies tokens.
5. Alice receives LP shares; pool `binTotals` and bin composition are permanently altered.

The bypass requires no special privileges — `allowedDepositor` is a public mapping, so any authorized address is trivially discoverable on-chain.

The correct pattern is already implemented in `SwapAllowlistExtension.beforeSwap`, which checks `sender` (the actual swapper):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

## Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may inject liquidity (e.g., KYC/compliance gating). Its bypass allows an unprivileged caller to alter `binTotals` and per-bin share distributions without authorization. Because bin composition directly determines the marginal bid/ask prices returned by `SwapMath`, this constitutes bad-price execution impact for subsequent swappers. If the attacker can also swap (no swap allowlist or they are on it), they can profit from the price distortion they created. This meets the "broken core pool functionality causing loss of funds" and "bad-price execution" impact criteria.

## Likelihood Explanation

The bypass requires a single direct call to `pool.addLiquidity` with any authorized address as `owner`. No flash loans, multi-step setup, or special privileges are needed. The `allowedDepositor` mapping is public, making authorized addresses trivially discoverable. The attack is immediately executable by any party aware of the pool's existence.

## Recommendation

Change `beforeAddLiquidity` to validate `sender` (the actual token provider) rather than `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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
  - Pool deployed with DepositAllowlistExtension on beforeAddLiquidity order.
  - alice is added to the allowlist: allowedDepositor[pool][alice] = true.
  - bob is NOT on the allowlist but implements IMetricOmmModifyLiquidityCallback.

Attack:
  1. bob calls pool.addLiquidity(alice, salt, deltas, callbackData, "").
  2. Pool calls _beforeAddLiquidity(msg.sender=bob, owner=alice, ...).
  3. Extension evaluates: allowedDepositor[pool][alice] == true → check passes.
  4. Pool calls bob.metricOmmModifyLiquidityCallback(...) to collect tokens.
  5. bob supplies tokens; alice receives LP shares.
  6. Pool binTotals and bin prices are modified by an unauthorized depositor.

Result:
  - bob successfully deposited into a deposit-gated pool.
  - The deposit allowlist invariant is broken.
  - Pool bin composition and marginal prices are altered without admin authorization.
```