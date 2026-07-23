All three key files are confirmed. The code matches the claim exactly:

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` (line 230-231).
2. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool, `sender` = router (line 37).
3. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router `msg.sender` at the pool (lines 72-80).

The claim is technically accurate and the exploit path is real.

Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` at the pool — the router address when users route through `MetricOmmSimpleRouter`. A pool admin who allowlists the router to support normal UX inadvertently grants every unprivileged caller of the router the ability to swap in the restricted pool, completely nullifying the access-control invariant the extension was deployed to enforce.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender`:**
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)` at line 230. `ExtensionCalling._beforeSwap` forwards this value unchanged as the `sender` argument to every configured extension.

**Step 2 — Router is `msg.sender` at the pool:**
`MetricOmmSimpleRouter.exactInputSingle` (line 72) calls `IMetricOmmPoolActions(params.pool).swap(...)` directly. The router contract is therefore `msg.sender` at the pool, not the end user. The same holds for `exactOutputSingle` (line 136) and `exactInput` (line 104).

**Step 3 — Extension checks the router, not the end user:**
`SwapAllowlistExtension.beforeSwap` (line 37) evaluates `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]`. The end user's address is never consulted.

**Step 4 — Allowlisting the router opens the gate to everyone:**
`setAllowedToSwap(pool, router, true)` is a natural operational step for any pool admin who wants their allowlisted users to swap via the canonical router. Once set, `allowedSwapper[pool][router] == true` satisfies the check for every caller of the router — any unprivileged address can call `router.exactInputSingle(...)` and execute swaps in the restricted pool.

Existing guards are insufficient: `allowAllSwappers` is a separate escape hatch and is not the issue; `onlyPoolAdmin` on `setAllowedToSwap` only restricts who can configure the allowlist, not the bypass itself.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (KYC, whitelist, institutional access). Once the pool admin allowlists the router — a necessary step to support normal UX — the guard is silently nullified. Any address can execute swaps in the restricted pool via the router. The pool's LP assets are exposed to the full public swap surface, defeating the access-control invariant the extension was deployed to enforce. This constitutes a broken core pool functionality causing potential loss of funds and an admin-boundary break where the extension's intended restriction is bypassed by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical user-facing entry point. Any pool admin who wants their allowlisted users to swap via the router must call `setAllowedToSwap(pool, router, true)` — there is no other mechanism to enable router-mediated swaps. This misconfiguration is a natural, expected operational step, not an exotic edge case. The bypass is reachable by any unprivileged address with no special setup beyond calling the public router.

## Recommendation
The extension must gate the economic actor (the end user), not the immediate `pool.swap()` caller. Two sound approaches:

1. **Router-forwarded identity via `extensionData`:** Define a convention where the router ABI-encodes `msg.sender` into `extensionData`; the extension decodes and checks that address. The router already forwards `extensionData` unchanged to the pool, which forwards it to extensions.
2. **Separate allowlist tier for intermediaries:** Maintain a second mapping `allowedRouter` and require that, when `sender` is a known router, the extension also validates a user address decoded from `extensionData`.

The extension must never treat an allowlisted intermediary as a blanket pass for all downstream callers.

## Proof of Concept
```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router so their whitelisted users can swap via UX
ext.setAllowedToSwap(pool, address(router), true);

// Attack: Bob (not individually allowlisted) routes through the router
address bob = makeAddr("bob");
// bob is NOT in allowedSwapper[pool][bob]
// but allowedSwapper[pool][router] == true

vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            pool,
    tokenIn:         token0,
    tokenOut:        token1,
    zeroForOne:      true,
    amountIn:        1_000,
    amountOutMinimum: 0,
    recipient:       bob,
    deadline:        block.timestamp + 1,
    priceLimitX64:   0,
    extensionData:   ""
}));
// ✓ swap succeeds — allowlist bypassed
// The extension saw sender == address(router), which is allowlisted
```

The pool calls `_beforeSwap(msg.sender=router, ...)`, the extension evaluates `allowedSwapper[pool][router] == true`, and Bob's swap executes without restriction.