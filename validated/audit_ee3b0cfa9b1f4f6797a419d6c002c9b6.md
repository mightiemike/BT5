Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`, i.e., the token payer) and instead checks `owner` (the LP share recipient). Because `addLiquidity` imposes no restriction on who may specify any `owner` value, any unprivileged address can bypass the deposit allowlist by passing an allowlisted address as `owner`, paying tokens via the callback, and having LP shares minted to that allowlisted address.

## Finding Description
In `MetricOmmPool.addLiquidity`, the before-hook is dispatched as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

So the extension receives `sender = msg.sender` (the actual depositor/payer) and `owner` (the LP share recipient). `DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (first parameter is unnamed) and checks `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

`addLiquidity` imposes **no restriction** on who may specify any `owner` value — unlike `removeLiquidity`, which enforces `msg.sender == owner`: [3](#0-2) 

An unauthorized caller B can pass `owner = A` (an allowlisted address). The extension evaluates `allowedDepositor[pool][A]` → `true`, the hook passes, B pays tokens via the swap callback, and A receives LP shares. The actual depositor identity the admin intended to gate (`sender`) is never checked.

The asymmetry with `SwapAllowlistExtension.beforeSwap` confirms this is the wrong variable: the swap guard correctly checks `sender` (the caller of `swap`), not `recipient`: [4](#0-3) 

The contract's own NatSpec states: *"Gates `addLiquidity` by depositor address, per pool."* The depositor is the address that pays tokens — `sender` — not the LP share recipient `owner`. [5](#0-4) 

## Impact Explanation
The deposit allowlist is an admin-configured access control meant to restrict which addresses may add liquidity to the pool. Because the check is on the wrong variable (`owner` instead of `sender`), any unprivileged address can bypass it by specifying an allowlisted `owner`. This breaks the pool admin's intended security boundary: unauthorized parties can inject liquidity into a restricted pool, manipulating bin state and per-share metrics. This constitutes a broken core pool access control mechanism — an admin-boundary break where an unprivileged caller bypasses an explicitly configured restriction, enabling unauthorized liquidity injection that can shift `totalShares` and bin balances in ways the admin explicitly sought to prevent.

## Likelihood Explanation
The bypass requires only a direct call to `pool.addLiquidity` with `owner` set to any allowlisted address. No special privileges, flash loans, or complex setup are needed. Any address can execute this at any time against any pool that has `DepositAllowlistExtension` configured. The attacker only needs to know one allowlisted address (which may be observable on-chain via `AllowedToDepositSet` events or `allowedDepositor` storage reads).

## Recommendation
Check `sender` (the actual depositor/caller) instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

## Proof of Concept
1. Deploy pool with `DepositAllowlistExtension` configured.
2. Admin calls `setAllowedToDeposit(pool, A, true)` — only address A is allowlisted; address B is not.
3. B calls `pool.addLiquidity(owner = A, salt, deltas, callbackData, extensionData)`.
4. Pool dispatches `extension.beforeAddLiquidity(sender=B, owner=A, ...)`.
5. Extension evaluates `allowedDepositor[pool][A]` → `true` → hook passes without checking B.
6. `LiquidityLib.addLiquidity` mints LP shares to A; B pays tokens via the callback.
7. B (unauthorized) has successfully added liquidity to the restricted pool, bypassing the allowlist entirely.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-11)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
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
