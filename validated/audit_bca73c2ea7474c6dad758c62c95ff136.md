Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual trader, enabling allowlist bypass for any user who routes through MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the first argument forwarded by the pool — which is the pool's own `msg.sender`, i.e. the router contract, not the end user. When a pool admin allowlists the router to permit router-mediated swaps, every unprivileged user can bypass the curated-pool allowlist by routing through `MetricOmmSimpleRouter`. Conversely, if the router is not allowlisted, legitimately allowlisted traders are silently blocked from using the router, breaking the primary swap interface.

## Finding Description

**Root cause — wrong actor bound in the hook:**

`SwapAllowlistExtension.beforeSwap` (line 37):
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
`msg.sender` here is the pool (enforced by `onlyPool` in `BaseMetricExtension`). `sender` is the first argument the pool passes when it calls the extension.

**How the pool populates `sender`:**

`ExtensionCalling._beforeSwap` (lines 149–177) encodes `sender` as the first positional argument and forwards it verbatim to every configured extension. The pool sets `sender = msg.sender` of its own `swap` call.

**Router call path:**

`MetricOmmSimpleRouter.exactInputSingle` (lines 72–80):
```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```
The router is `msg.sender` at the pool boundary. Therefore `sender` delivered to the extension is the **router address**, not the end user. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Exploit flow:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` attached.
2. Admin allowlists the router (`allowedSwapper[pool][router] = true`) so that users can reach the pool through the standard periphery.
3. Any unprivileged user calls `router.exactInputSingle(pool, ...)`.
4. Pool calls `_beforeSwap(sender=router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
6. Non-allowlisted user executes a swap on a pool that was supposed to be gated.

**Existing guards are insufficient:**

`BaseMetricExtension.onlyPool` only verifies that the caller is a registered pool; it does not recover the original EOA. There is no mechanism in the extension or the pool to propagate the true initiating address through the router hop.

## Impact Explanation

A curated pool's swap allowlist is completely bypassed for any user who routes through `MetricOmmSimpleRouter`. The allowlist is the sole access-control mechanism for such pools; bypassing it allows disallowed traders to execute swaps, draining pool liquidity at oracle prices and violating the pool's curation policy. This constitutes a broken core pool functionality and a direct admin-boundary break reachable by any unprivileged caller. Severity: **High**.

## Likelihood Explanation

The router is the primary user-facing swap interface documented and expected by the protocol. A pool admin who wants users to interact via the router must allowlist it, which is the natural operational choice. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle`. The condition (router allowlisted on a curated pool) is a foreseeable and likely production configuration.

## Recommendation

Pass the true initiating user through the call chain. Two options:

1. **Preferred:** Add an explicit `swapper` parameter to the pool's `swap` function (or a dedicated field in `extensionData`) that the router populates with `msg.sender` before calling the pool. The pool forwards this value as `sender` to `_beforeSwap`, and the extension checks it.
2. **Alternative:** In `SwapAllowlistExtension.beforeSwap`, ignore the `sender` argument and instead require callers to embed a signed proof of the real user's identity in `extensionData`, verified inside the hook.

Do not rely on `msg.sender` of the pool call as the gated identity when a router intermediary is part of the supported call path.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // 1. Deploy pool with SwapAllowlistExtension
    // 2. Admin allowlists the router: swapExt.setAllowedToSwap(pool, address(router), true)
    // 3. Confirm attacker is NOT allowlisted: assertFalse(swapExt.isAllowedToSwap(pool, attacker))
    // 4. Attacker calls router
    vm.prank(attacker);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // 5. Assert swap succeeded despite attacker not being on the allowlist
    // Extension checked allowedSwapper[pool][router] == true, not allowedSwapper[pool][attacker]
}
```