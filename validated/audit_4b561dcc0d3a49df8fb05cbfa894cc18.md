Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Ignores `sender`, Allowing Any Unpermissioned Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` receives the actual caller (`sender`) as its first argument but silently discards it, checking only `owner` (the position holder) against the allowlist. Because `MetricOmmPool.addLiquidity` permits `msg.sender ≠ owner` with no additional guard, any unpermissioned address can bypass the deposit allowlist by nominating an allowlisted address as `owner`, causing unauthorized tokens to enter a restricted pool and breaking the core access-control invariant.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as separate arguments to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

There is no `require(msg.sender == owner)` guard anywhere in `addLiquidity`. The pool explicitly supports an operator pattern where the payer and position holder differ.

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both addresses to the extension:

```solidity
// ExtensionCalling.sol lines 95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first positional parameter but leaves it unnamed and never reads it:

```solidity
// DepositAllowlistExtension.sol lines 32-41
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Here `msg.sender` is the pool address (the extension's caller), so the check resolves to `allowedDepositor[pool][owner]`. The actual operator/payer is never validated.

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` with no restriction beyond `owner != address(0)`:

```solidity
// MetricOmmPoolLiquidityAdder.sol lines 56-68
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);   // only checks != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
```

**Exploit path:**
1. Pool P has `DepositAllowlistExtension`; only address A is allowlisted (`allowedDepositor[P][A] = true`).
2. Unpermissioned address B calls `LiquidityAdder.addLiquidityExactShares(pool=P, owner=A, ...)`.
3. The adder calls `P.addLiquidity(owner=A, ...)` with `msg.sender = LiquidityAdder`.
4. The pool calls `extension.beforeAddLiquidity(sender=LiquidityAdder, owner=A, ...)`.
5. The extension checks `allowedDepositor[P][A]` → `true` → passes.
6. B's tokens enter pool P; LP shares are minted to A.

The same path works when B calls `pool.addLiquidity(owner=A, ...)` directly.

## Impact Explanation

The deposit allowlist is the primary access-control mechanism for restricted pools (KYC-gated, institutional, or regulatory pools). Its bypass means:
- Unauthorized token flows enter a pool that is supposed to be gated, violating the core invariant that "only approved addresses may deposit."
- If A and B collude, A can call `removeLiquidity` (which enforces `msg.sender == owner`) and return the tokens to B, making the allowlist completely ineffective as a deposit guard.
- Even without collusion, B can force unwanted LP positions onto A and inject unauthorized funds into the pool, breaking compliance guarantees the pool admin intended to enforce.

This constitutes a broken core pool access-control mechanism with a direct path to unauthorized fund flows into restricted pools.

## Likelihood Explanation

- `MetricOmmPoolLiquidityAdder` is the standard periphery entry point and explicitly exposes an `owner` parameter any caller can set to any non-zero address.
- No special privilege is required; the attacker only needs to know one allowlisted address, which is observable on-chain from prior `AllowedToDepositSet` events.
- The bypass is reachable by any EOA or contract in a single transaction with no preconditions beyond token approval.

## Recommendation

Check `sender` (the actual operator/payer) in addition to or instead of `owner`, depending on intended semantics:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    bool poolAllowsAll = allowAllDepositors[msg.sender];
    if (!poolAllowsAll && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate the position holder, the `owner` check is correct but the `sender` bypass must be explicitly documented. If the intent is to gate the depositing operator (the actual payer), `sender` must be checked.

## Proof of Concept

```solidity
// Pool P has DepositAllowlistExtension; only `allowedUser` is allowlisted.
// `attacker` is NOT allowlisted.

address allowedUser = ...; // allowedDepositor[P][allowedUser] == true
address attacker    = ...; // allowedDepositor[P][attacker]    == false

vm.prank(attacker);
liquidityAdder.addLiquidityExactShares(
    pool,
    allowedUser,   // allowlisted owner; attacker is the actual payer
    salt,
    deltas,
    maxAmount0,
    maxAmount1,
    ""
);
// Succeeds: extension checks allowedDepositor[P][allowedUser] == true
// attacker's tokens enter pool P without authorization
```