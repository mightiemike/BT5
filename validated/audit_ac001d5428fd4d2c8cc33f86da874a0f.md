Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router address, not the original user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every unprivileged user can bypass the swap allowlist by calling through the router.

## Finding Description
**Call path:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — `msg.sender` at the pool is the router address.
3. Pool's `swap` calls `_beforeSwap(msg.sender, recipient, ...)`, so `sender = router_address`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router_address` and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router_address]`.

If the router is not allowlisted, step 5 reverts even for legitimately allowlisted users who go through the router. If the pool admin allowlists the router to fix this, step 5 passes for **any** caller, because the checked identity is the router, not the original user.

**Root cause:** `MetricOmmPool.swap` passes `msg.sender` (the direct caller) as `sender` to the extension hook. The router does not forward the original user's identity. `SwapAllowlistExtension` has no mechanism to recover the original user from the router's transient context.

**Existing guards are insufficient:** The `_requireExpectedCallbackCaller` check in the router only validates that the callback comes from the expected pool; it does not inject the original user's address into the swap call. There is no on-chain path for the extension to distinguish a router-mediated swap from a direct swap.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. This breaks the core access-control invariant of the extension: unauthorized swappers can execute trades on a pool that is intended to be permissioned. The impact is broken core pool functionality (allowlist gate) and potential unauthorized fund flows through a restricted pool.

## Likelihood Explanation
Any unprivileged user can exploit this by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting a pool with `SwapAllowlistExtension`. The only precondition is that the router is allowlisted on that pool, which is a necessary operational step for any allowlisted user to use the router. The attack is repeatable, requires no special privileges, and is reachable from any public EOA or contract.

## Recommendation
Pass the original user's address through the router to the pool, or have the pool recover it from a trusted transient context. One approach: add an optional `originator` field to the swap call that the router populates with `msg.sender` before calling the pool, and have the pool forward that as `sender` to extensions when set. Alternatively, `SwapAllowlistExtension` should check both `sender` and a router-provided originator, or the pool admin documentation must explicitly warn that allowlisting the router opens the gate to all users.

## Proof of Concept
```
1. Deploy a pool with SwapAllowlistExtension as a before-swap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true) — only alice is allowed.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router is allowlisted so alice can use it.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle with the pool as target.
5. Router calls pool.swap(...); pool passes sender = router_address to beforeSwap.
6. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
7. Bob's swap executes successfully, bypassing the allowlist.

Foundry test: assert that a non-allowlisted EOA calling exactInputSingle on an allowlist-gated pool succeeds when the router is allowlisted, and that removing the router from the allowlist causes allowlisted users' router calls to revert.
```