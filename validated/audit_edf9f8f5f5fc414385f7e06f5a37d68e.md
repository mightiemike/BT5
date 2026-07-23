Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unpermissioned depositor to bypass the curated-pool allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the address that initiated the `addLiquidity` call and whose tokens fund the pool) and only checks `owner` (the LP share recipient). Because `MetricOmmPoolLiquidityAdder.addLiquidityWeighted` allows any caller to freely specify an arbitrary `owner`, an address not on the allowlist can deposit into a curated pool by naming any allowlisted address as `owner`, with zero privileged access required.

## Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` is declared with an unnamed first positional argument (`sender`) that is never read:

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

`ExtensionCalling._beforeAddLiquidity` correctly passes both `sender` and `owner` to the hook:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

`MetricOmmPoolLiquidityAdder.addLiquidityWeighted` (the `owner`-overload) allows any caller to specify an arbitrary `owner`:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L100-115
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);  // only checks owner != address(0)
    ...
    return _addLiquidity(pool, owner, salt, scaled, msg.sender, ...);
}
```

**Exploit call chain:**
1. Pool admin deploys pool with `DepositAllowlistExtension`; calls `setAllowedToDeposit(pool, bob, true)`. Alice is not allowlisted.
2. Alice calls `MetricOmmPoolLiquidityAdder.addLiquidityWeighted(pool, bob, salt, delta, ...)`.
3. The router calls `pool.addLiquidity(bob, ...)` — pool's `msg.sender` is the router (`sender = liquidityAdder`).
4. Pool calls `_beforeAddLiquidity(sender=liquidityAdder, owner=bob, ...)`.
5. Hook checks `allowedDepositor[pool][bob]` → `true` → no revert.
6. Alice's tokens (pulled via `pay(token, alice, pool, amount)` in the callback) enter the pool; bob receives LP shares.
7. Alice has deposited into a pool that was supposed to exclude her.

The `_validateOwner` check at L247-249 only rejects `address(0)`, imposing no allowlist constraint. No existing guard checks the actual payer identity against the allowlist.

## Impact Explanation
The deposit allowlist is the primary curation mechanism for pools restricting who may provide liquidity (KYC-gated, regulatory-compliance, or curated LP sets). Bypassing it means unpermissioned funds enter the pool in direct violation of the pool admin's token-flow policy. A colluding pair (alice pays, bob receives shares) can launder alice's participation through bob's allowlisted identity. If the allowlist is used to exclude destabilizing large depositors, the bypass removes that protection entirely. This constitutes a direct policy bypass with fund-level consequences on curated pools.

## Likelihood Explanation
No privileged access is required. Any address can call `MetricOmmPoolLiquidityAdder.addLiquidityWeighted` with an arbitrary `owner`. The attacker only needs to know one allowlisted address, which is publicly readable from the `allowedDepositor` mapping. The bypass is unconditional and works on every curated pool using `DepositAllowlistExtension`. The existing test `test_weighted_canAddOnBehalfOfAnotherOwner` (L303-314) already demonstrates the exact precondition (alice paying, bob as owner) without any allowlist in place, confirming the path is fully exercised.

## Recommendation
Check `sender` (the actual initiator of the `addLiquidity` call, i.e., the router) instead of — or in addition to — `owner` in `beforeAddLiquidity`. Since the router is the `sender` at the pool level, the allowlist should also cover the payer identity passed through the router's callback context, or the pool admin should allowlist the router and rely on the router to enforce per-user checks. At minimum, the hook must not gate solely on `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender]
        && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept
1. Deploy pool with `DepositAllowlistExtension` configured.
2. Pool admin calls `setAllowedToDeposit(pool, bob, true)` — bob allowlisted; alice not.
3. Alice calls `MetricOmmPoolLiquidityAdder.addLiquidityWeighted(pool, bob, salt, delta, cap0, cap1, ...)`.
4. Router calls `pool.addLiquidity(bob, ...)` → pool dispatches `_beforeAddLiquidity(sender=router, owner=bob, ...)`.
5. Hook evaluates `allowedDepositor[pool][bob]` → `true` → passes without revert.
6. Callback pulls alice's tokens into the pool; bob receives LP shares.
7. Assert: alice's token balance decreased; bob's LP shares increased; alice was never on the allowlist.

This is directly reproducible as a Foundry integration test extending the existing `MetricOmmPoolLiquidityAdder.t.sol` setup by adding a `DepositAllowlistExtension` to the pool and verifying the deposit succeeds for a non-allowlisted payer.