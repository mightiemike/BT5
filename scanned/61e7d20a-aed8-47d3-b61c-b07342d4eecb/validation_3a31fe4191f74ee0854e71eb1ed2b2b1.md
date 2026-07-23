### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. A pool admin who allowlists the router to support router-based swaps for approved users inadvertently opens the pool to every user who can call the router, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` receives this value as `sender` and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

The router stores the original `msg.sender` only in its internal transient callback context for payment purposes; it never forwards the user's identity to the pool. The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

A pool admin who wants to support router-based swaps for allowlisted users must add the router to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the guard passes for every caller of the router regardless of their identity. The allowlist is fully bypassed.

---

### Impact Explanation

**High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to approved counterparties (e.g., KYC'd addresses, institutional partners, or protocol-controlled accounts) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against the pool's LP reserves, extracting value at oracle-anchored prices that the pool admin intended to offer only to approved parties. LP principal is directly at risk because the pool's token reserves are consumed by trades that the allowlist was designed to block.

---

### Likelihood Explanation

**High.** The router is the standard, documented periphery entry point for swaps. Any pool admin who configures a swap allowlist and also wants to support the router (the expected user-facing path) will naturally add the router to the allowlist. The bypass requires no special knowledge: any user calls `exactInputSingle` on the router pointing at the curated pool. No privileged access, no malicious setup, and no non-standard token behavior is required.

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary. Two approaches:

1. **Pass the original caller through the router.** Add a `swapper` field to the router's swap parameters and forward it as part of `extensionData` or a dedicated argument. The extension then reads the declared swapper from `extensionData` and verifies it against the allowlist. This requires a coordinated change to the router and extension.

2. **Gate on `recipient` or require direct pool calls for curated pools.** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce direct `pool.swap` calls for allowlisted pools. This is simpler but limits usability.

The cleanest fix consistent with the existing architecture is option 1: the router stores `msg.sender` in transient storage already (for payment); it should also expose it as a verifiable identity that extensions can consume.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in the `beforeSwap` hook order.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-based swaps (a natural operational step).
3. Pool admin does **not** add `attacker` to the allowlist: `allowedSwapper[pool][attacker] == false`.
4. `attacker` calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Swap executes. `attacker` receives output tokens from the curated pool despite never being allowlisted.

The allowlist invariant — "only approved addresses may swap" — is broken for every user who routes through the public router.