Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` instead of `sender`, allowing any disallowed address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and evaluates only `owner` (the position owner) against the allowlist. Because `owner` is a free caller-supplied parameter with no access restriction beyond a non-zero check, any disallowed address can bypass the allowlist by naming an allowed address as `owner`. The actual token payer — the address that matters for KYC/compliance gating — is never checked.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` (the direct pool caller, i.e. `msg.sender` at the pool level) as its first argument, but the parameter is unnamed and discarded. The allowlist check is performed only on `owner`:

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

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`, with no restriction on what `owner` may be:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` explicitly allows the caller to supply an arbitrary `owner` distinct from `msg.sender`. The only validation on `owner` is a non-zero check (`_validateOwner`). The actual payer (`msg.sender`) is stored separately in transient storage and used only in the callback to pull tokens:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L65-67
_validateOwner(owner);  // only checks owner != address(0)
_validateDeltas(deltas);
return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
```

In the callback, tokens are pulled from the stored `payer` (the original `msg.sender` of the adder), not from `owner`:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L162-177
(address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
...
if (amount0Delta > 0) { pay(token0, payer, msg.sender, amount0Delta); }
if (amount1Delta > 0) { pay(token1, payer, msg.sender, amount1Delta); }
```

The complete exploit path:
1. Disallowed address A calls `liquidityAdder.addLiquidityExactShares(pool, owner=B, ...)` where B is an allowed address.
2. Adder stores `payer = A` in transient storage, then calls `pool.addLiquidity(owner=B, ...)`.
3. Pool calls `extension.beforeAddLiquidity(sender=LiquidityAdder, owner=B, ...)`.
4. Extension evaluates `allowedDepositor[pool][B]` → `true` → **no revert**.
5. Pool invokes the callback; adder pulls tokens from A and sends them to the pool.
6. Shares are minted under position `(B, salt)`.

A's tokens are deposited into the restricted pool despite A being disallowed. The check on `owner` is trivially satisfied by any caller who knows one allowed address (publicly readable from `allowedDepositor`).

## Impact Explanation

The deposit allowlist is completely ineffective. Any disallowed address can deposit tokens into a restricted pool by specifying any allowed address as `owner`. The disallowed address pays the tokens; the allowed address receives the shares. The pool admin's intent to restrict who may deposit — for KYC, compliance, or permissioned liquidity provisioning — is fully circumvented. This breaks the core access-control functionality of `DepositAllowlistExtension` and any pool that relies on it.

## Likelihood Explanation

The bypass requires only knowledge of one allowed address (publicly readable from `allowedDepositor`) and a single call to `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with `owner` set to that address. No privileged access, no special token behavior, and no off-chain data are needed. Any disallowed address can execute this immediately and repeatedly.

## Recommendation

Check `sender` (the direct pool caller) instead of `owner` in `beforeAddLiquidity`:

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

Note that when `MetricOmmPoolLiquidityAdder` is used, `sender` will be the adder contract address, not the end-user payer. If end-user gating through the adder is also required, the adder must forward the original `msg.sender` via `extensionData`, and the extension must decode and check it.

## Proof of Concept

1. Deploy a pool with `DepositAllowlistExtension` as `extension1`.
2. Pool admin calls `setAllowedToDeposit(pool, B, true)` — only address B is allowed.
3. Address A (not in the allowlist) calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(pool, /*owner=*/B, salt, deltas, max0, max1, "");
   ```
4. Execution path:
   - `LiquidityAdder` stores `payer = A` in transient storage via `_setPayContext`.
   - `pool.addLiquidity(owner=B, ...)` is called with `msg.sender = LiquidityAdder`.
   - Pool calls `extension.beforeAddLiquidity(sender=LiquidityAdder, owner=B, ...)`.
   - Extension evaluates `allowedDepositor[pool][B]` → `true` → no revert.
   - Pool calls `LiquidityAdder.metricOmmModifyLiquidityCallback(...)`.
   - Adder loads `payer = A` from transient storage and pulls tokens from A.
   - Shares are minted under position `(B, salt)`.
5. Assert: A's token balance decreased, B holds new shares, and the deposit succeeded despite A being disallowed.