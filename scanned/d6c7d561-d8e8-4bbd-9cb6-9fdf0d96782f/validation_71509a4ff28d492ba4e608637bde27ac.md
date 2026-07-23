### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router contract as `msg.sender`, so the allowlist checks the router address rather than the actual user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user, bypassing the curated-pool access control entirely.

### Finding Description

`SwapAllowlistExtension.beforeSwap()` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap()`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = pool's msg.sender
    )
);
```

The pool populates `sender` with its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

**Direct call path** (`user → pool.swap()`): `sender = user`. The allowlist correctly checks `allowedSwapper[pool][user]`.

**Router call path** (`user → router.exactInputSingle() → pool.swap()`): the router calls `pool.swap()` with no forwarding of the original caller, so `msg.sender` to the pool is the router contract:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // user-supplied bytes, NOT the user's address
    );
```

The pool therefore calls `_beforeSwap(router, ...)`, and the extension checks `allowedSwapper[pool][router]` — the router address, not the actual user.

This creates two mutually exclusive failure modes:

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router; they must call the pool directly. Core swap functionality is broken for the supported periphery path. |
| Router **allowlisted** (to enable router-mediated swaps) | Every user — including those the admin explicitly excluded — can bypass the allowlist by routing through the router. |

The `SwapAllowlistExtension` ignores the `extensionData` bytes entirely, so there is no existing mechanism for the router to forward the real caller's identity to the guard.

### Impact Explanation

When the pool admin allowlists the router address to permit router-mediated swaps, any unprivileged user can bypass the curated-pool access control by calling `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput` / `exactOutput`). The allowlist — intended to restrict swaps to KYC'd, whitelisted, or otherwise vetted counterparties — is rendered inoperative. Non-allowlisted users gain full swap access to a pool whose LP positions were sized and priced under the assumption of a restricted counterparty set, directly exposing LP principal to adverse selection from unintended traders.

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the standard, publicly documented periphery swap path. Any pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router, which simultaneously opens the pool to all users. The attack requires no special privileges, no oracle manipulation, and no flash loan — only a call to the public router.

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData` before calling the pool, and `SwapAllowlistExtension.beforeSwap()` decodes and checks that address when `sender` is a known router.
2. **Sender override in router**: The router passes the real user address as the `recipient` or via a dedicated field that the pool forwards to extensions, and the extension checks that field.

Until fixed, pool admins using `SwapAllowlistExtension` must document that allowlisted users must call the pool directly and must never allowlist the router address.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls: setAllowedToSwap(pool, router, true)
    // admin intends to allow router-mediated swaps for allowlisted users

Attack:
  attacker (not individually allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})
      → router calls pool.swap() with msg.sender = router
      → pool calls _beforeSwap(router, ...)
      → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
      → swap executes — allowlist bypassed

Result:
  attacker swaps on a curated pool they were never authorized to access.
  LP principal is exposed to an unintended counterparty.
```