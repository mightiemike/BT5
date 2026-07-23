Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool receives `sender = router`, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every public user the ability to bypass the individual-user allowlist, allowing unauthorized swappers to execute swaps against a pool intended to be access-controlled and drain LP reserves.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the allowlist check as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // sender seen by the extension
    recipient, ...
);
```

`MetricOmmSimpleRouter` must call `pool.swap(...)` directly because the pool calls `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)` to settle the trade — the router must be `msg.sender` to receive this callback. Therefore, inside `pool.swap`, `msg.sender` is always the **router address**, not the end user. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The structural trap: a pool admin deploying `SwapAllowlistExtension` to restrict swaps to a curated set of addresses must also allowlist the `MetricOmmSimpleRouter` if they want any router-mediated swap to succeed. The moment the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every user who routes through it, regardless of whether that user is individually allowlisted. There is no mechanism to allowlist specific users for router-mediated swaps — the admin must choose between blocking all router paths or opening them to everyone.

Existing guards are insufficient: `BaseMetricExtension` provides an `onlyPool` modifier, but `SwapAllowlistExtension.beforeSwap` overrides the base without it, and the check itself only validates the immediate caller of `pool.swap`, not the economic actor initiating the trade.

## Impact Explanation

Any user not individually allowlisted can swap in a pool intended to be access-controlled by routing through the public `MetricOmmSimpleRouter`. Unauthorized swaps against a restricted pool allow the attacker to execute swaps at oracle-derived bid/ask prices that LPs did not consent to offer to the general public, and to drain token0 or token1 reserves from LP positions deposited under the assumption that only vetted counterparties could trade. This constitutes a direct loss of LP principal — pool token balances are reduced by the unauthorized swap output, and LPs cannot recover those funds. Severity is **High/Critical**: broken core pool access-control functionality causing direct loss of LP assets; pool balances fail to cover LP claims under the intended access model.

## Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. A pool admin who configures `SwapAllowlistExtension` and also wants to support router-mediated swaps — a common and expected use case — will allowlist the router. The bypass requires no privileged access, no special tokens, and no malicious setup: only a call to the public router targeting the restricted pool. The condition is trivially reachable and repeatable by any address.

## Recommendation

The extension must check the economically relevant actor — the end user — not the immediate caller of `pool.swap`. Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into the `extensionData` it passes to the pool. Update `SwapAllowlistExtension.beforeSwap` to decode and check this address when `sender` is a known, trusted router. This requires a trust assumption on the router encoding.

2. **Document and enforce direct-call-only**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and require users to call `pool.swap` directly. This limits UX but preserves the invariant.

The cleanest long-term fix is approach (1), with the extension verifying the decoded address against the allowlist instead of (or in addition to) `sender`.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwapOrder`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary for any router-mediated swap to work.
3. Pool admin calls `setAllowedToSwap(pool, alice, false)` — Alice is explicitly not allowlisted.
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(router, alice_recipient, ...)`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → **passes**.
8. Alice's swap executes, draining LP reserves she was never authorized to access.

Contrast: Alice calls `pool.swap(...)` directly → `sender = alice` → `allowedSwapper[pool][alice] == false` → **reverts**. The outcome is inconsistent depending solely on which entry point Alice uses.