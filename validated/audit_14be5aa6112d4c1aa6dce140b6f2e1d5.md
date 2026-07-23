Audit Report

## Title
`SwapAllowlistExtension` bypassed for all router-mediated swaps: `sender` is always the router, not the originating user - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
In every router-mediated swap path (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, and all recursive hops in `_exactOutputIterateCallback`), the `sender` argument forwarded to `beforeSwap` is `msg.sender` of the pool's `swap` call, which is always the `MetricOmmSimpleRouter` contract address. The `SwapAllowlistExtension` checks `allowedSwapper[pool][sender]`, so it gates on the router address rather than the originating user. A pool admin who allowlists the router to permit any router-mediated swap simultaneously grants every unprivileged user the ability to bypass the per-user allowlist.

## Finding Description

**Exact call flow for `exactOutput` (2-hop example):**

1. User calls `router.exactOutput(params)` — `msg.sender` to the router is the user.
2. Router calls `lastPool.swap(params.recipient, ...)` (line 165) — `msg.sender` inside `lastPool.swap` is the **router**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` (line 230-240) — passes **router** as `sender` to the extension.
4. `lastPool` calls `router.metricOmmSwapCallback(...)` — `msg.sender` inside the callback is `lastPool`.
5. Router dispatches to `_exactOutputIterateCallback` (line 58).
6. `_exactOutputIterateCallback` calls `nextPool.swap(msg.sender, ...)` (line 220-228) — here `msg.sender` is `lastPool` (used as `recipient`), but the **caller** of `nextPool.swap` is still the **router**.
7. `MetricOmmPool.swap` again calls `_beforeSwap(msg.sender, ...)` — passes **router** as `sender`.

In `ExtensionCalling._beforeSwap` (line 149-177), `sender` is forwarded verbatim to the extension:
```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
where `msg.sender` = pool and `sender` = **router**. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Why existing guards are insufficient:** The `_requireExpectedCallbackCaller` check (line 49) only validates that the callback comes from the expected pool — it does not recover the original user identity. There is no mechanism in the pool or extension interface to propagate the original EOA through the router's callback chain.

## Impact Explanation
The `SwapAllowlistExtension` is the protocol's designated per-pool swap access control gate. A pool admin who wants to allow legitimate users to swap via the router must allowlist the router address. Once the router is allowlisted, every unprivileged address — including those the admin explicitly excluded — can call any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and pass the allowlist check, because the extension always sees `sender = router`. This is an admin-boundary break: the pool admin's intended per-user restriction is fully bypassed by any unprivileged caller routing through the public router.

## Likelihood Explanation
The condition is reachable by any unprivileged user with no special setup: they simply call the public router instead of the pool directly. The only prerequisite is that the pool admin has allowlisted the router (a necessary step to allow any router-mediated swap for legitimate users). This is a repeatable, low-cost bypass available to any address on every block.

## Recommendation
The extension must receive the originating user identity, not the immediate `msg.sender` of the pool. Options:
1. Pass the original user address through `extensionData` and have the router encode it; the extension decodes and verifies it. This requires a trusted encoding convention.
2. Add an `originator` field to the pool's `swap` interface and thread it through `_beforeSwap` alongside `sender`.
3. Document that `SwapAllowlistExtension` gates the direct pool caller only and is incompatible with router-mediated swaps, and provide a separate extension that reads originator identity from a trusted transient-storage slot written by the router before each hop.

## Proof of Concept
```solidity
// Pool configured with SwapAllowlistExtension; router is allowlisted, attacker is not.
// allowedSwapper[pool][router] = true
// allowedSwapper[pool][attacker] = false

// Attacker bypasses allowlist:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// beforeSwap receives sender=router → allowedSwapper[pool][router]=true → passes
// Attacker swaps successfully despite being excluded from the allowlist.
```