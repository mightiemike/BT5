Audit Report

## Title
SwapAllowlistExtension Bypassed by Router-Mediated Swaps When Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the direct caller of `pool.swap()` — the router address when `MetricOmmSimpleRouter` is used, not the end user. If a pool admin allowlists the router (a reasonable action to enable router UX features for trusted users), every unpermissioned user can bypass the per-user allowlist by routing through the same public router contract. The end user's identity is stored only in transient storage for the payment callback and is never forwarded to the pool or extension.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  ...
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = direct caller of `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is called, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The original user's identity (`msg.sender` of the router call) is stored only in transient storage via `_setNextCallbackContext` for the payment callback — it is never forwarded to the pool or extension as `sender`. The pool sees `sender` = router address. The `multicall` path uses `delegatecall` internally but the external `pool.swap()` call still originates from the router contract address.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` as a `beforeSwap` hook; `allowAllSwappers[pool] = false`.
2. Admin sets `allowedSwapper[pool][trustedUser] = true` for specific KYC'd/whitelisted addresses.
3. Admin sets `allowedSwapper[pool][router] = true` so trusted users can access router UX (multicall, ETH wrapping, exact-output, multi-hop).
4. Unpermissioned attacker calls `router.exactInputSingle(pool, ...)`.
5. Pool calls `extension.beforeSwap(sender=router, ...)` → `allowedSwapper[pool][router] = true` → check passes.
6. Attacker's swap executes on the allowlist-protected pool.

The extension has no mechanism to distinguish "router called by trusted user" from "router called by anyone."

## Impact Explanation

`SwapAllowlistExtension` is the primary on-chain mechanism for pool admins to restrict swap access to KYC'd counterparties, whitelisted market makers, or specific protocol integrators. Bypassing it allows arbitrary users to execute swaps on pools designed to be access-controlled. Concrete fund impact: if the allowlist protects LPs from toxic order flow (e.g., informed traders, MEV bots), bypass allows those actors to trade against LP positions at oracle-derived prices, causing direct LP principal loss. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break" criteria.

## Likelihood Explanation

The precondition — admin allowlisting the router — is a realistic and reasonable admin action. Any admin who wants their trusted users to use router features (multicall, ETH wrapping, exact-output, multi-hop) while still restricting direct pool access would naturally allowlist the router. The extension's design gives no indication that doing so opens the pool to all router users. Once the router is allowlisted, the bypass is trivially repeatable by any unpermissioned user with zero additional preconditions.

## Recommendation

The extension should verify the end user, not the intermediary. Two approaches:

1. **Forward the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`, and the extension reads and verifies it. This requires router cooperation and trust in the router not to spoof the value.

2. **Check `extensionData` for a signed or trusted user identity**: The extension reads a user address from `extensionData` and verifies it against the allowlist, falling back to `sender` when `extensionData` is empty (direct calls). The router must be modified to always populate `extensionData` with the real caller.

3. **Document the limitation explicitly**: If the design intent is that `sender` = direct caller, document that allowlisting a public router grants access to all router users, and pool admins should not allowlist public routers on allowlist-protected pools.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension, only trustedUser and router allowlisted
    allowlistExt.setAllowedToSwap(pool, trustedUser, true);
    allowlistExt.setAllowedToSwap(pool, address(router), true); // admin enables router UX

    // Attacker (not allowlisted) calls router
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        tokenIn: token0,
        extensionData: ""
    }));
    // Swap succeeds — allowlist bypassed
}
```