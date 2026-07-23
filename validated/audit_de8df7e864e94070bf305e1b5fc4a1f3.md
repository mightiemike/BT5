Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks LP-share recipient (`owner`) instead of token payer (`sender`), allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards its first `address` parameter (the actual token payer, passed as `msg.sender` from `addLiquidity`) and gates only the `owner` argument (the LP-share recipient). Because `owner` is freely caller-controlled in `MetricOmmPool.addLiquidity`, any non-allowlisted address can name an allowlisted address as `owner` and deposit into a restricted pool without restriction.

## Finding Description

`MetricOmmPool.addLiquidity` passes the real caller as the first argument to the hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, ...)` but leaves `sender` unnamed and never reads it:

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

The allowlist mapping is keyed by `(pool, depositor)`:

```solidity
mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
``` [3](#0-2) 

Because `owner` is a free caller-supplied argument to `addLiquidity` with no constraint tying it to `msg.sender`, the gate is structurally bypassed: the extension evaluates `allowedDepositor[pool][owner]` where `owner` is attacker-chosen, not `allowedDepositor[pool][sender]` where `sender` is the economically relevant actor paying the tokens.

The `MetricOmmPoolLiquidityAdder` further enables the split: `addLiquidityExactShares(pool, owner, ...)` accepts an arbitrary `owner` distinct from `msg.sender` with only a zero-address check: [4](#0-3) [5](#0-4) 

## Impact Explanation

A pool configured with `DepositAllowlistExtension` is intended to be a private liquidity venue. The bypass breaks this invariant: a non-allowlisted attacker who controls a second allowlisted wallet calls `pool.addLiquidity(allowlistedWallet, ...)`, passes the extension check, has tokens pulled from themselves, and LP shares minted to the allowlisted wallet. They then call `removeLiquidity` from the allowlisted wallet to recover the tokens — having deposited into a restricted pool with no restriction. Even without controlling the allowlisted address, the attacker can force-inject liquidity, diluting existing LPs' fee share and disrupting any per-share metric used by stop-loss or oracle-guard extensions. This is a broken access-control invariant with direct LP-asset impact (unauthorized share minting, fee dilution).

## Likelihood Explanation

Requires no special privilege — any EOA or contract can call `addLiquidity` directly on the pool. Allowlisted addresses are publicly visible on-chain via `AllowedToDepositSet` events. The `MetricOmmPoolLiquidityAdder` provides a ready-made path to separate payer from owner with no additional barrier. The attack is repeatable and requires only knowledge of one allowlisted address.

## Recommendation

Change `beforeAddLiquidity` to check the first parameter (the actual token payer) rather than `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to restrict both who pays and who receives shares, check both `sender` and `owner`.

## Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached with `beforeAddLiquidity` order set.
2. Admin calls `setAllowedToDeposit(pool, allowlistedWallet, true)` — only `allowlistedWallet` is permitted.
3. Attacker (non-allowlisted EOA controlling `allowlistedWallet`) calls:
   ```solidity
   pool.addLiquidity(allowlistedWallet, salt, delta, callbackData, extensionData);
   ```
4. Pool calls `extension.beforeAddLiquidity(attacker, allowlistedWallet, ...)`.
5. Extension evaluates `allowedDepositor[pool][allowlistedWallet]` → `true` → no revert.
6. LP shares are minted to `allowlistedWallet`; tokens are transferred from the attacker.
7. Attacker calls `pool.removeLiquidity(allowlistedWallet, ...)` from `allowlistedWallet` — `msg.sender == owner` check passes — and recovers the tokens, having successfully deposited into a restricted pool.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-13)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
