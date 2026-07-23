Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of actual caller/payer, allowing any non-allowlisted address to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards its first parameter (the actual caller/payer) and checks only the `owner` (position recipient) against the allowlist. Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address supplied by the caller, any non-allowlisted address can bypass the deposit gate by naming an allowlisted address as the position owner. The pool admin's access-control invariant — that only allowlisted addresses may deposit — is broken.

## Finding Description

`MetricOmmPool.addLiquidity` invokes the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The first argument is `msg.sender` — the address that will pay tokens via the callback. The second is `owner` — the address that will receive the LP position.

`DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter unnamed and never reads it:

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

The allowlist lookup is `allowedDepositor[pool][owner]`, not `allowedDepositor[pool][caller]`. The actual payer is never checked.

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` provides a clean public entry point that explicitly separates `owner` from `msg.sender` (payer): [3](#0-2) 

Inside `_addLiquidity`, the pool is called with `positionOwner = owner` (the attacker-supplied allowlisted address): [4](#0-3) 

The extension then sees `owner` = allowlisted address → passes. The non-allowlisted `msg.sender` pays tokens via callback (`payer` stored in transient context), but is never checked against the allowlist. [5](#0-4) 

## Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (KYC/AML compliance, curated LP sets, regulatory perimeters). With this bug, a non-allowlisted address can deposit tokens into a restricted pool as the economic depositor without being on the allowlist. The allowlisted `owner` receives a funded LP position they did not pay for; `removeLiquidity` enforces `msg.sender == owner`, so the allowlisted owner can withdraw the full token value. This constitutes a broken core pool access-control invariant with direct fund impact: unauthorized addresses interact economically with restricted pools, and allowlisted addresses receive unearned LP assets. This meets the admin-boundary break criterion. [6](#0-5) 

## Likelihood Explanation

No special privilege is required — any EOA or contract can call `addLiquidity` or `addLiquidityExactShares`. Allowlisted addresses are publicly discoverable from on-chain `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. [7](#0-6) 

The `MetricOmmPoolLiquidityAdder` provides a clean, public entry point that explicitly separates `owner` from `msg.sender`, making the bypass trivially reachable. Likelihood is high whenever a pool is deployed with `DepositAllowlistExtension` in a non-`allowAllDepositors` configuration.

## Recommendation

Check the actual caller (first parameter) instead of `owner`:

```solidity
function beforeAddLiquidity(address caller, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][caller]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This ensures the address that actually pays tokens — not the address that receives the position — is the one gated by the allowlist. [2](#0-1) 

## Proof of Concept

1. Deploy a pool with `DepositAllowlistExtension` configured (`allowAllDepositors[pool] = false`).
2. Pool admin calls `setAllowedToDeposit(pool, Alice, true)`. Bob is not allowlisted.
3. Bob calls `pool.addLiquidity(Alice, salt, deltas, callbackData, extensionData)` directly.
4. Pool calls `_beforeAddLiquidity(Bob, Alice, ...)` → extension checks `allowedDepositor[pool][Alice]` → `true` → no revert.
5. Pool calls `Bob.metricOmmModifyLiquidityCallback(amount0, amount1, ...)` → Bob pays tokens.
6. Alice's position is credited with the deposited shares.
7. Alice calls `pool.removeLiquidity(Alice, salt, deltas, ...)` and withdraws the full token value.
8. Bob has deposited into a restricted pool without being allowlisted; Alice received a funded position she did not pay for.

Alternatively via `MetricOmmPoolLiquidityAdder`: Bob calls `addLiquidityExactShares(pool, Alice, salt, deltas, max0, max1, extensionData)` — `_validateOwner` only checks `owner != address(0)`, not allowlist membership — achieving the same bypass through the public router. [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-177)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
