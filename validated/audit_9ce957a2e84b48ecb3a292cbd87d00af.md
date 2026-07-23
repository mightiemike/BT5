Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of actual caller/payer, allowing any non-allowlisted address to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards its first parameter (the actual caller passed by the pool) and gates only the `owner` (position recipient) against the allowlist. Because `addLiquidity` accepts an arbitrary `owner` address, any non-allowlisted address can bypass the gate by naming an allowlisted address as the position owner while acting as the economic depositor. The pool admin's primary access-control invariant — that only allowlisted addresses may deposit — is broken.

## Finding Description

`MetricOmmPool.addLiquidity` invokes the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The first argument is `msg.sender` — the address that will pay tokens via `metricOmmModifyLiquidityCallback`. The second argument is `owner` — the address that will receive the LP position.

`DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter as unnamed and never reads it:

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

The lookup `allowedDepositor[msg.sender][owner]` uses `msg.sender` as the pool key (correct) but `owner` as the depositor key (wrong — this is the recipient, not the payer).

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` explicitly separates `owner` from `msg.sender` (the payer):

```solidity
function addLiquidityExactShares(address pool, address owner, uint80 salt, ...) external payable override {
    _validateOwner(owner);   // only checks owner != address(0)
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

`_validateOwner` only rejects `address(0)`: [4](#0-3) 

Inside `_addLiquidity`, the pool is called with `positionOwner = owner` (the supplied allowlisted address), so the extension sees the allowlisted owner and passes: [5](#0-4) 

Token payment is pulled from `payer` (the actual non-allowlisted caller) in the callback: [6](#0-5) 

`removeLiquidity` enforces `msg.sender == owner`, so the allowlisted owner can withdraw the full token value: [7](#0-6) 

## Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (KYC/AML compliance, curated LP sets, regulatory perimeters). With this bug, a non-allowlisted address can act as the economic depositor into a restricted pool without being on the allowlist. The allowlisted owner receives a funded LP position they did not pay for and can withdraw the full token value. The pool admin's access-control invariant is broken by any unprivileged caller who knows one allowlisted address (discoverable from on-chain `AllowedToDepositSet` events). This constitutes a broken core pool functionality and an admin-boundary break.

## Likelihood Explanation

No special privilege is required; any EOA or contract can call `pool.addLiquidity(allowlistedOwner, ...)` directly or route through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedOwner, ...)`. Allowlisted addresses are publicly discoverable from emitted `AllowedToDepositSet` events. The `MetricOmmPoolLiquidityAdder` provides a clean, public entry point that explicitly separates `owner` from `msg.sender`. Likelihood is high whenever a pool is deployed with `DepositAllowlistExtension` in a non-`allowAllDepositors` configuration.

## Recommendation

Check the actual caller (the first parameter) instead of `owner`:

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

This ensures the address that actually pays tokens — not the address that receives the position — is the one gated by the allowlist.

## Proof of Concept

1. Deploy a pool with `DepositAllowlistExtension` configured (`allowAllDepositors[pool] = false`).
2. Pool admin calls `setAllowedToDeposit(pool, Alice, true)`. Bob is not allowlisted.
3. Bob calls `pool.addLiquidity(Alice, salt, deltas, callbackData, extensionData)` directly.
4. Pool calls `_beforeAddLiquidity(Bob, Alice, ...)` → extension checks `allowedDepositor[pool][Alice]` → `true` → no revert.
5. Pool calls `Bob.metricOmmModifyLiquidityCallback(amount0, amount1, ...)` → Bob pays tokens.
6. Alice's position is credited with the deposited shares.
7. Alice calls `pool.removeLiquidity(Alice, salt, deltas, ...)` and withdraws the full token value.
8. Bob has deposited into a restricted pool without being allowlisted; Alice received a funded position she did not pay for.

Alternatively via the liquidity adder: Bob calls `liquidityAdder.addLiquidityExactShares(pool, Alice, salt, deltas, max0, max1, extensionData)` — the adder stores Bob as payer in transient storage, calls `pool.addLiquidity(Alice, ...)`, the extension checks Alice (allowlisted), passes, and the callback pulls tokens from Bob.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
