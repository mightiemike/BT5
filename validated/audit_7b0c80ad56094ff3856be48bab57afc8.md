Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `pool.addLiquidity`) and gates on `owner`, which is a free caller-supplied parameter with no identity constraint. Any non-allowlisted address can bypass the deposit restriction by passing any allowlisted address as `owner`, paying tokens via the callback, and mutating pool state while the allowlist check passes on the allowlisted `owner`.

## Finding Description
**Root cause:** `DepositAllowlistExtension.beforeAddLiquidity` (L32–42) names the first parameter `address` (unnamed/discarded) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

**Call path:**
1. `MetricOmmPool.addLiquidity` (L191) calls `_beforeAddLiquidity(msg.sender, owner, ...)` — `msg.sender` is the actual caller, `owner` is the caller-supplied position-owner address with no constraint beyond non-zero.
2. `ExtensionCalling._beforeAddLiquidity` (L95–98) forwards both verbatim via `abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, ...))`.
3. The extension receives `sender = attacker` (first arg, unnamed, discarded) and `owner = allowlistedAlice` (second arg, checked). The check passes.
4. `LiquidityLib.addLiquidity` (L147–148) calls `IMetricOmmModifyLiquidityCallback(msg.sender).metricOmmModifyLiquidityCallback(...)` — the attacker's contract pays tokens and pool state (bin balances, `binTotals`, cursor) is mutated.

**Existing guards are insufficient:** `_validateOwner` in `MetricOmmPoolLiquidityAdder` (L247–249) is only a zero-address check. `removeLiquidity` enforces `msg.sender == owner` (L206), but `addLiquidity` has no such constraint. The `onlyPool` modifier on the extension only verifies the caller is a registered pool, not the actor identity.

**Contrast with `SwapAllowlistExtension`:** The swap allowlist correctly checks `sender` (L37): `!allowedSwapper[msg.sender][sender]`, confirming the deposit allowlist's use of `owner` is an implementation error.

## Impact Explanation
The `DepositAllowlistExtension` is the sole production mechanism for curated pools to restrict depositors. The bypass is complete: any non-allowlisted address can deposit into a curated pool by nominating any allowlisted address as `owner`. The attacker pays tokens and mutates pool state — bin balances, `binTotals.scaledToken0/1`, and cursor position — without authorization. The allowlisted address receives unsolicited LP shares (grief requiring active removal). An attacker can use this to move the pool cursor into a targeted bin before a victim's trade, achieving price manipulation. This breaks the core invariant that curated pools enforce: only allowlisted addresses may interact with the deposit path.

## Likelihood Explanation
Exploitation requires only a direct call to `pool.addLiquidity` with any allowlisted address as `owner`. No privileged access, no special token, no oracle manipulation is needed. The allowlist is publicly readable via `allowedDepositor`. The attacker must hold tokens and implement `IMetricOmmModifyLiquidityCallback`, both trivial conditions. The attack is repeatable at will.

## Recommendation
Check `sender` (the actual depositor) instead of `owner`, mirroring `SwapAllowlistExtension`:

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

When deposits are routed through `MetricOmmPoolLiquidityAdder`, `sender` will be the adder's address; pool admins must allowlist the adder and rely on the adder's own payer logic, or require direct pool calls only.

## Proof of Concept
```solidity
// Setup: pool configured with DepositAllowlistExtension.
// Pool admin has allowlisted `alice` but NOT `attacker`.

contract Attacker is IMetricOmmModifyLiquidityCallback {
    function exploit(address pool, address alice, LiquidityDelta calldata deltas) external {
        // owner = alice (allowlisted) → extension passes; sender = address(this) (not allowlisted) → ignored
        IMetricOmmPoolActions(pool).addLiquidity(alice, 0, deltas, "", "");
    }

    function metricOmmModifyLiquidityCallback(
        uint256 amount0Delta, uint256 amount1Delta, bytes calldata
    ) external {
        IERC20(token0).transfer(msg.sender, amount0Delta);
        IERC20(token1).transfer(msg.sender, amount1Delta);
    }
}
// Result: allowlist bypassed, LP shares minted to alice, pool state mutated by attacker.
```

A Foundry integration test can fork the deployment, configure `DepositAllowlistExtension` with only `alice` allowlisted, deploy `Attacker`, fund it with tokens, call `exploit`, and assert that `LiquidityAdded` is emitted and `attacker` is not in the allowlist — confirming the bypass.