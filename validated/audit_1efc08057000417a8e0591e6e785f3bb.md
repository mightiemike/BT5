### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the originating user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is a production `beforeSwap` hook that restricts which addresses may execute swaps on a pool. The hook checks the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the natural configuration to let allowlisted users reach the pool through the periphery), every public user of the router inherits the router's allowlisted status and can bypass the gate entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap
    recipient, zeroForOne, amountSpecified, priceLimitX64,
    packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other swap entry point on the router) calls `pool.swap` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

The router stores the original `msg.sender` only in transient callback context for token settlement; it is never forwarded to the pool as the `sender` identity. The extension therefore sees `sender = router`, not `sender = end user`.

**Bypass path:**

1. Pool admin deploys pool with `SwapAllowlistExtension`, sets `allowAllSwappers[pool] = false`, and allowlists a set of approved users.
2. To let those approved users reach the pool through the periphery, the admin also calls `setAllowedToSwap(pool, router, true)`.
3. Any unapproved user calls `router.exactInputSingle(...)` targeting the restricted pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`. The extension checks `allowedSwapper[pool][router]` → `true` → passes.
5. The unapproved user's swap executes against the restricted pool.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access on a pool. Once bypassed, every public caller of `MetricOmmSimpleRouter` (or any other contract the admin allowlisted as a proxy) can execute swaps that the pool admin explicitly intended to block. This breaks the core access-control invariant of the extension and constitutes broken core pool functionality: the allowlist gate is rendered ineffective for all router-mediated swaps.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. This is the natural and expected configuration: without it, even approved users cannot reach the pool through the periphery router, making the allowlist operationally unusable for any real deployment that relies on the standard periphery. A pool admin who reads the `SwapAllowlistExtension` interface in isolation has no indication that allowlisting the router grants access to all router users rather than only the approved subset. The likelihood of this misconfiguration is high for any pool that uses both the allowlist extension and the standard router.

---

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary contract. Two viable approaches:

1. **Pass originating user in `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and check it. This requires a coordinated convention between router and extension.
2. **Check `sender` only for direct pool calls; reject router-mediated calls**: The extension can detect router-mediated calls (e.g., by checking whether `sender` is a known router) and revert unless the extension data carries a signed or otherwise authenticated user identity.
3.