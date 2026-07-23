Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Position `owner` Instead of Caller `sender`, Allowing Any Actor to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`, who provides tokens via callback) and instead validates `owner` (the caller-supplied LP position recipient). Because `owner` is a free parameter chosen by the caller, any un-allowlisted actor can pass an allowlisted address as `owner` to trivially bypass the guard. The deposit allowlist's core invariant is completely broken.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` parameter as `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly forwards both addresses to the extension hook: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then silently discards `sender` (unnamed first parameter) and checks only `owner`: [3](#0-2) 

Since `owner` is freely chosen by the caller with no constraint (unlike `removeLiquidity`, which enforces `msg.sender == owner`), any un-allowlisted actor can supply an allowlisted address as `owner`. The extension evaluates `allowedDepositor[pool][allowlisted_address]` → `true` and does not revert. The pool then calls back to the actual caller (`sender`) for tokens via the swap callback, and the allowlisted address receives the LP position.

The correct pattern is demonstrated by `SwapAllowlistExtension.beforeSwap`, which checks `sender` (the actual swapper): [4](#0-3) 

No existing guard prevents this. `addLiquidity` imposes no constraint that `msg.sender == owner`: [5](#0-4) 

## Impact Explanation

The deposit allowlist's core invariant — only allowlisted addresses may provide liquidity — is completely broken. A pool admin who deploys this extension to enforce KYC/compliance or restrict liquidity providers achieves zero protection. Any actor who knows a single allowlisted address (observable on-chain via `AllowedToDepositSet` events or direct `allowedDepositor` reads) can deposit arbitrary amounts. This is an admin-boundary break: an unprivileged path bypasses a pool-admin-configured access control, matching the allowed impact gate.

## Likelihood Explanation

Exploitation requires no special privileges, no signatures, no timing constraints, and no excess allowances. The attacker only needs to know one allowlisted address and call `addLiquidity(owner=allowlisted_address, ...)`. Allowlisted addresses are publicly observable on-chain. The attack is repeatable indefinitely.

## Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

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

If the intent is to allowlist position owners rather than token providers, the parameter name and documentation must be updated to reflect that, and the router layer must enforce `sender == owner` for direct deposits.

## Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; only `Alice` is allowlisted (`allowedDepositor[pool][Alice] = true`).
2. `Bob` (not allowlisted) calls `pool.addLiquidity(owner=Alice, salt=0, deltas=..., callbackData=..., extensionData=...)`.
3. The pool calls `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
4. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes; the pool calls back to `Bob` for tokens; `Bob` transfers tokens to the pool.
6. Alice receives the LP position; Bob has deposited into a pool that was supposed to reject him.
7. The pool admin's allowlist is completely bypassed with zero privileged access.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
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
