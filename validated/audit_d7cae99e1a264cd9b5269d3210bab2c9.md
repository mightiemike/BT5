Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Recipient (`owner`) Instead of Actual Depositor (`sender`), Allowing Complete Allowlist Bypass - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the `msg.sender` of `addLiquidity`, who pays tokens via callback) and instead gates on `owner` (the caller-supplied LP-share recipient). Because `owner` is a free parameter with no on-chain binding to the token source, any unprivileged address can bypass the allowlist by passing an allowlisted address as `owner`. The pool admin's deposit restriction is rendered completely ineffective.

## Finding Description

`MetricOmmPool.addLiquidity` captures `msg.sender` and the caller-supplied `owner` separately: [1](#0-0) 

It forwards both to `ExtensionCalling._beforeAddLiquidity`: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, ...)` but discards `sender` (unnamed first parameter) and checks only `owner`: [3](#0-2) 

The admin-facing setter names the gated address `depositor`, confirming the intended semantics are to gate the actual depositing party, not the LP-share recipient: [4](#0-3) 

The contract NatSpec also states it "Gates `addLiquidity` by depositor address": [5](#0-4) 

Because `owner` is a free caller-supplied parameter, any address Bob can call `pool.addLiquidity(Alice, salt, deltas, callbackData, extensionData)` where Alice is allowlisted. The guard evaluates `allowedDepositor[pool][Alice]` → `true` → no revert. The pool then executes `LiquidityLib.addLiquidity` with `owner = Alice`, pulling tokens from Bob via the swap-callback mechanism and crediting LP shares to Alice. [6](#0-5) 

## Impact Explanation

The deposit allowlist is completely ineffective. Any address, regardless of allowlist status, can deposit tokens into a restricted pool by supplying an allowlisted address as `owner`. For pools configured as private, institutional, or KYC-gated liquidity venues, the core access-control invariant is broken. The unauthorized depositor loses their tokens (pulled via callback), the allowlisted address receives LP shares it never requested, and the pool admin's restriction is violated. This constitutes a broken core pool functionality causing loss of funds (unauthorized token extraction from the depositor) and an admin-boundary break (allowlist bypass by an unprivileged path).

## Likelihood Explanation

The bypass requires only a single `addLiquidity` call with a publicly observable allowlisted address as `owner`. The `allowedDepositor` mapping is public, so any actor can enumerate allowlisted addresses on-chain. No special privileges, flash loans, or multi-step setup are needed. The attack is immediately executable by any EOA or contract.

## Recommendation

Replace the `owner` check with a `sender` check to gate the actual depositing party:

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

If the intent is instead to gate LP-position ownership (restrict who may *hold* shares), the NatSpec, setter parameter name (`depositor`), and event name (`AllowedToDepositSet`) must all be updated to reflect `owner` semantics unambiguously.

## Proof of Concept

1. Pool admin deploys pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, Alice, true)`. Bob is **not** allowlisted.
2. Bob calls `pool.addLiquidity(Alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `_beforeAddLiquidity(Bob /*sender*/, Alice /*owner*/, ...)` → extension receives `(Bob, Alice, ...)`.
4. Extension discards `Bob` (unnamed first arg) and evaluates `allowedDepositor[pool][Alice]` → `true` → **no revert**.
5. Pool executes `LiquidityLib.addLiquidity(..., owner=Alice, ...)`, callback pulls tokens from Bob, LP shares credited to Alice.
6. Bob has deposited into a pool he is not allowlisted for; the allowlist is fully bypassed.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L192-194)
```text
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-12)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
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
