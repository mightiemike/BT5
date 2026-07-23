Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on `owner` Instead of `sender`, Allowing Allowlist Bypass — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and checks only `owner`, which is a freely caller-supplied address. Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any unprivileged caller can name an allowlisted address as `owner`, pass the guard, and deposit tokens into a restricted pool while never being checked themselves.

## Finding Description
`MetricOmmPool.addLiquidity` passes two distinct actors into the hook pipeline:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the real depositor; `owner` is an arbitrary address supplied by the caller. There is no `require(msg.sender == owner)` anywhere in `addLiquidity` — that check exists only in `removeLiquidity` (L206).

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both actors to the extension:

```solidity
// ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it unnamed and never reads it. The allowlist check is performed exclusively on `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

After the hook passes, `LiquidityLib.addLiquidity` mints shares to `owner`'s position key and then invokes the token-transfer callback on `msg.sender` (the real depositor):

```solidity
// LiquidityLib.sol L147-148
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```

The library is called via delegatecall from the pool, so `msg.sender` here is the original external caller — the unauthorized depositor — who pays the tokens. The position is recorded under `owner`.

## Impact Explanation
The deposit allowlist is the sole on-chain mechanism for restricting who may provide liquidity to a pool. With this bug, any address can call `addLiquidity(owner = allowlisted_address, ...)`. The guard passes because the allowlisted address satisfies `allowedDepositor[pool][owner]`, while the actual depositor (`msg.sender`) is never verified. The unauthorized depositor pays the tokens via the callback, the position is minted to the allowlisted address, and the pool's liquidity composition is altered by an actor the pool admin explicitly excluded. If the allowlisted address is a contract controlled by the attacker, the position can subsequently be withdrawn, completing a full deposit-and-withdraw cycle that entirely circumvents the restriction. This constitutes a broken access-control guard with direct fund-flow consequences and breaks the core invariant of the extension.

## Likelihood Explanation
Exploitation requires no special privilege. Any EOA or contract can call `addLiquidity` with an arbitrary `owner`. The only prerequisite is knowing at least one allowlisted address, which is publicly readable via `allowedDepositor(pool, address)`. The attacker can also self-allowlist by deploying a contract, setting it as `owner`, and controlling it to cooperate on `removeLiquidity`. Likelihood is high.

## Recommendation
Check `sender` (the first, currently unnamed parameter) instead of — or in addition to — `owner`:

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

If the intent is also to restrict who may own a position, add a separate check on `owner`.

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** allowlisted.
3. Bob deploys a contract implementing `IMetricOmmModifyLiquidityCallback` and calls:
   ```solidity
   pool.addLiquidity(
       alice,        // owner — allowlisted, passes the guard
       salt,
       deltas,
       callbackData, // Bob's contract pays tokens here
       extensionData
   );
   ```
4. `_beforeAddLiquidity` fires; the extension checks `allowedDepositor[pool][alice] == true` → no revert.
5. `LiquidityLib.addLiquidity` mints shares to Alice's position key; Bob's callback transfers tokens into the pool.
6. Bob has deposited into a pool he is explicitly barred from, bypassing the allowlist entirely. If Alice is a contract Bob controls, he calls `pool.removeLiquidity(alice, ...)` from that contract to recover the tokens.