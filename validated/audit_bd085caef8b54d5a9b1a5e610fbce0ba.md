Audit Report

## Title
`DepositAllowlistExtension` gates `owner` instead of `sender`, enabling full allowlist bypass via `MetricOmmPoolLiquidityAdder` - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller of `addLiquidity`) and only gates on `owner` (the position recipient). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a fully caller-controlled `owner` with only a zero-address check, any unprivileged user can bypass the deposit allowlist by supplying an allowlisted address as `owner`, paying the tokens themselves, and receiving nothing while the allowlisted address receives LP shares. The pool admin's intent to restrict who can interact with the pool is completely violated.

## Finding Description

**Root cause — wrong identity gated.**

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but discards it (unnamed parameter). The check uses `msg.sender` (the pool address, since the pool calls the extension) as the mapping key for the pool, and `owner` as the depositor identity:

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

The first unnamed `address` parameter is `sender` — the `msg.sender` of the pool's `addLiquidity` call, i.e. the actual depositor. It is never read.

**Pool passes `msg.sender` as `sender` and caller-supplied `owner` as `owner`.**

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**`MetricOmmPoolLiquidityAdder` accepts a fully caller-controlled `owner`.**

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(
    address pool,
    address owner,   // ← fully caller-controlled
    ...
) external payable override returns (...) {
    _validateOwner(owner);  // only checks owner != address(0)
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
```

`_validateOwner` only rejects `address(0)`:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L247-249
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
```

**Exploit call chain:**

1. Attacker calls `adder.addLiquidityExactShares(pool, allowlisted_address, salt, deltas, max0, max1, extensionData)`
2. Adder calls `pool.addLiquidity(allowlisted_address, salt, deltas, abi.encode(KIND_PAY), extensionData)`
3. Pool calls `_beforeAddLiquidity(msg.sender=adder, owner=allowlisted_address, ...)`
4. Extension checks `allowedDepositor[pool][allowlisted_address]` → **passes** (allowlisted address is allowlisted)
5. Pool mints LP shares to `allowlisted_address`; adder pulls tokens from attacker via callback
6. Attacker (non-allowlisted) has successfully deposited into the pool

The same bypass applies to `addLiquidityWeighted(pool, owner, ...)` at L88-116, which also accepts a caller-controlled `owner` with only `_validateOwner` validation.

## Impact Explanation

The deposit allowlist invariant — that only allowlisted addresses can cause pool state changes via `addLiquidity` — is completely broken. Any unprivileged EOA can:

- Add liquidity to any bin, moving the pool cursor and altering bin balances, directly affecting values consumed by `OracleValueStopLossExtension._afterSwapOracleStopLoss` (potentially suppressing or triggering stop-loss watermark breaches).
- Force LP positions onto allowlisted addresses without their consent (griefing).
- Render KYC/compliance-gated pools entirely ineffective, as any actor can deposit tokens into the pool regardless of allowlist status.

This constitutes a broken core pool functionality (admin-configured access control bypassed by an unprivileged path) with direct impact on pool state integrity and regulatory compliance guarantees.

## Likelihood Explanation

- `MetricOmmPoolLiquidityAdder` is a public, permissionless contract with no factory verification.
- The bypass requires zero privileged access: any EOA can call `addLiquidityExactShares` with a known allowlisted address as `owner`.
- The attacker only needs to know one allowlisted address. The pool admin address is publicly readable from the factory, and is typically allowlisted.
- The attack is repeatable indefinitely with no cooldown or cost beyond gas and token amounts.

## Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` should gate on the `sender` argument (the actual caller of `addLiquidity`) rather than — or in addition to — `owner`. The first parameter should be named and used:

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

When the pool is called through `MetricOmmPoolLiquidityAdder`, `sender` will be the adder contract address, so the adder itself would need to be allowlisted, or the adder should forward the real payer identity through `extensionData` for the hook to decode and verify. Alternatively, `MetricOmmPoolLiquidityAdder` should enforce `owner == msg.sender` when no trusted intermediary is involved.

## Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; only `allowedUser` is allowlisted.
// attacker is NOT allowlisted.

address allowedUser = ...; // known allowlisted address (e.g., pool admin)
address attacker    = ...; // not allowlisted

// Direct attempt — correctly blocked:
vm.prank(attacker);
pool.addLiquidity(attacker, salt, deltas, callbackData, extensionData);
// → reverts NotAllowedToDeposit ✓

// Bypass via MetricOmmPoolLiquidityAdder:
vm.prank(attacker);
adder.addLiquidityExactShares(
    address(pool),
    allowedUser,   // owner = allowlisted address → check passes
    salt,
    deltas,
    maxAmount0,
    maxAmount1,
    extensionData
);
// → succeeds; attacker paid tokens, allowedUser received LP shares,
//   pool state (cursor, bin balances) modified by non-allowlisted actor ✗
```