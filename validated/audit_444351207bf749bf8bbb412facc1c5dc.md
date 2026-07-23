All four cited code locations are confirmed against the actual repository. The vulnerability is real:

1. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the pool's `msg.sender` — confirmed at L37.
2. `MetricOmmPool.swap` passes its own `msg.sender` as `sender` to `_beforeSwap` — confirmed at L230-231.
3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender` — confirmed at L72-80.
4. `ExtensionCalling._beforeSwap` forwards `sender` verbatim — confirmed at L149-177.

---

Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool always sets to its own `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router, not the end user. Any pool admin who allowlists the router (required for router-mediated swaps) inadvertently grants every non-allowlisted user the ability to bypass the curated-pool gate by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs:
```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
`msg.sender` here is the pool (correct key), and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap` (L230-231), the pool always passes its own `msg.sender` as `sender` to `_beforeSwap`. When `MetricOmmSimpleRouter.exactInputSingle` (L72-80) calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, the pool's `msg.sender` becomes the router address. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. `ExtensionCalling._beforeSwap` (L149-177) confirms `sender` is forwarded verbatim with no transformation. The pool admin faces an impossible choice: not allowlisting the router blocks all router-mediated swaps for legitimate users; allowlisting the router opens the gate to every address on-chain.

## Impact Explanation
Any non-allowlisted user can execute swaps on a curated pool (e.g., KYC-gated or institution-only) by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` whenever the router is allowlisted. The swap executes at the live oracle price, draining pool liquidity and generating fees that the pool was designed to restrict to specific counterparties. This is a direct bypass of the pool's access-control invariant, constituting a broken core pool functionality with direct fund impact on restricted pools.

## Likelihood Explanation
The router is the primary user-facing swap interface. Any pool that wants to support router-mediated swaps for its allowlisted users must add the router to the allowlist. Once added, the bypass is unconditionally available to every address on-chain with no additional preconditions — no special role, no flash loan, no oracle manipulation. A single `exactInputSingle` call suffices.

## Recommendation
The `sender` argument passed to `beforeSwap` must represent the economic actor, not the immediate caller. Two complementary fixes:
1. **Router-side**: Have `MetricOmmSimpleRouter` encode `msg.sender` (the end user) into `extensionData` on every swap call so allowlist extensions can recover the true actor.
2. **Extension-side**: Require the router to forward the originating user in `extensionData`, and have `SwapAllowlistExtension` decode and check that address when `sender` is a known router.

Alternatively, document and enforce at the factory level that `SwapAllowlistExtension` is incompatible with router usage (e.g., reject pool creation that configures both a swap allowlist extension and a router-compatible price provider).

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension in the beforeSwap hook order.
2. Pool admin allowlists Alice:
       swapExtension.setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router so Alice can use it:
       swapExtension.setAllowedToSwap(pool, router, true)
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap() → pool's msg.sender = router
   → extension checks allowedSwapper[pool][router] == true → PASSES
   → Bob's swap executes on the curated pool.
5. Bob receives tokens from the restricted pool with no allowlist enforcement.
```