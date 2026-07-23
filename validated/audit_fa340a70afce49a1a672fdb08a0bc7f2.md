Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity()` checks caller-controlled `owner` instead of actual depositor `sender`, allowing full allowlist bypass - (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity()` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and gates access only on the caller-supplied `owner` argument. Because `owner` is a free parameter any caller can set to any allowlisted address, the deposit allowlist — the sole access-control mechanism for restricted pools — is completely bypassed by any unprivileged address in a single transaction.

## Finding Description

`MetricOmmPool.addLiquidity()` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity()` correctly encodes both values and forwards them to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity()` receives `sender` as its first argument but leaves it unnamed (discarded), then checks only `owner`: [3](#0-2) 

The token pull happens via callback on `msg.sender` (the actual depositor), not on `owner`: [4](#0-3) 

Shares are credited to the position key derived from `owner`, and `removeLiquidity` enforces `msg.sender == owner`, so only `owner` can withdraw: [5](#0-4) 

**Exploit path:** Bob (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`. The extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert. Bob's tokens are pulled via the modify-liquidity callback. Shares are credited to `positionBinShares[keccak256(alice, salt, bin)]`. Bob has deposited into a restricted pool without being on the allowlist. Alice holds the position and can call `removeLiquidity` to claim Bob's tokens. Bob's loss is irrecoverable.

## Impact Explanation

This is a direct, irrecoverable loss of user principal (Bob's deposited tokens) and a complete bypass of the admin-configured deposit allowlist. The allowlist is the sole access-control gate for restricted pools; its bypass means any external address can deposit into pools the admin intended to restrict to a curated set of LPs. The secondary consequence — Bob's tokens permanently locked under Alice's position key, claimable by Alice — constitutes a theft of principal. This meets the Critical/High threshold under the allowed impact gate (direct loss of user principal; admin-boundary break via unprivileged path).

## Likelihood Explanation

- No special role or prior approval is required; any EOA or contract can call `addLiquidity` with an arbitrary `owner`.
- The only prerequisite is knowing one allowlisted address, which is trivially discoverable on-chain from `AllowedToDepositSet` events or by querying `allowedDepositor`.
- The attack is a single transaction with no setup cost beyond gas and token approval.
- The condition is permanent until the extension code is fixed; every restricted pool using `DepositAllowlistExtension` is affected.

## Recommendation

Check `sender` (the actual depositor) instead of `owner` in `beforeAddLiquidity`. The intent of the allowlist is to gate who provides liquidity, which is the `sender`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

If the intent is to restrict both who calls and who benefits, check both `sender` and `owner`.

## Proof of Concept

1. Deploy pool with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
3. Bob deploys a contract implementing `IMetricOmmModifyLiquidityCallback` that transfers the required tokens in `metricOmmModifyLiquidityCallback`.
4. Bob calls `pool.addLiquidity(alice, 0, deltas, callbackData, "")`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` credits shares to `positionBinShares[keccak256(alice, 0, bin)]` and pulls Bob's tokens via callback.
7. Bob has deposited into a restricted pool without being on the allowlist. Alice calls `removeLiquidity` to drain Bob's tokens. Bob's principal is irrecoverably lost.

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```
