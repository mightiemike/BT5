Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller) and instead checks `owner` (the position owner, which is caller-supplied). Any unprivileged address can bypass the deposit allowlist by calling `addLiquidity` with `owner` set to any address that is on the allowlist, since the extension will see an allowlisted `owner` and pass the check. This renders the pool admin's primary access-control mechanism entirely ineffective.

## Finding Description
In `DepositAllowlistExtension.beforeAddLiquidity` (lines 32–42), the first parameter (`sender`) is unnamed and ignored. The guard checks `allowedDepositor[msg.sender][owner]`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

In `MetricOmmPool.addLiquidity` (line 191), the pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

Because `owner` is a free parameter chosen by the caller, any address can pass any allowlisted address as `owner`. The extension sees `allowedDepositor[pool][allowlistedAddress] = true` and does not revert. The unauthorized caller then pays tokens via the swap callback, and the position is recorded under the allowlisted address.

`SwapAllowlistExtension.beforeSwap` (lines 31–41) correctly checks `sender` and ignores `recipient`, confirming the asymmetry is a bug and not an intentional design choice. The admin-facing setter `setAllowedToDeposit` also uses the name `depositor`, not `owner`, further confirming the intent was to gate by caller.

## Impact Explanation
The deposit allowlist is the pool admin's primary mechanism to restrict which addresses may add liquidity. With this bug the guard is entirely ineffective: any address can deposit into a restricted pool by nominating any allowlisted address as `owner`. This satisfies the **Admin-boundary break** impact gate: a pool-admin-configured access control is bypassed by an unprivileged path. Concrete consequences include unauthorized liquidity injection into restricted pools (shifting bin cursor positions and altering internal price state), and positions being created under allowlisted addresses without their consent, breaking KYC/compliance or LP-participation invariants the pool admin intended to enforce.

## Likelihood Explanation
Exploitation requires no special privileges. Any address can call `addLiquidity` on a pool with `DepositAllowlistExtension` in `BEFORE_ADD_LIQUIDITY_ORDER`, passing any allowlisted address (publicly readable from `allowedDepositor`) as `owner`. The only cost is supplying the token amounts via the callback. There is no other barrier. The attack is repeatable at will.

## Recommendation
Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`, consistent with `SwapAllowlistExtension.beforeSwap`:

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

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension` configured in `beforeAddLiquidityOrder`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** on the allowlist.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(bob /*msg.sender*/, alice /*owner*/, ...)` → extension receives `(bob, alice, ...)`.
5. Extension ignores `bob` (unnamed first param) and checks `allowedDepositor[pool][alice]` = `true` → **no revert**.
6. `LiquidityLib.addLiquidity` records the position under `alice`.
7. Pool calls `IMetricOmmSwapCallback(bob).metricOmmSwapCallback(...)` — Bob pays the tokens.
8. Bob has successfully deposited into a restricted pool, bypassing the allowlist entirely.