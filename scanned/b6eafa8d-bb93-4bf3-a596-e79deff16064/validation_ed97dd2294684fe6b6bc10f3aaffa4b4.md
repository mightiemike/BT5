### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper — Any User Can Bypass Per-User Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the user. If the pool admin allowlists the router (the only way to let allowlisted users trade through the router), every unprivileged user can bypass the per-user allowlist by routing through the same router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the first parameter — the immediate caller of `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The router does not forward the original user's address. The pool therefore sees `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]`.

**The trap**: if the pool admin wants allowlisted users to be able to trade through the router, they must allowlist the router address. But once `allowedSwapper[pool][router] = true`, the check passes for every caller of the router — including addresses that were never individually allowlisted. The per-user gate is fully bypassed.

The `DepositAllowlistExtension` does not share this flaw: it checks the `owner` parameter (the position owner), which is explicitly supplied by the caller and is the economically relevant party regardless of who the payer is.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., a private institutional pool, a KYC-gated pool, or a pool with a curated set of market-makers) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against LP funds. In an oracle-anchored pool, LP exposure to oracle latency or manipulation is the primary risk; unauthorized traders can exploit that window, causing direct loss of LP principal.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a natural and expected configuration step: without it, even individually allowlisted users cannot trade through the router (their swaps revert because `allowedSwapper[pool][router]` is false). An admin who wants to support router-mediated swaps for their allowlisted users has no other option. The configuration choice that enables the intended use case simultaneously enables the bypass for all users.

---

### Recommendation

The `SwapAllowlistExtension` must gate the actual end-user, not the immediate caller of `pool.swap()`. Two approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` in `extensionData`; have the extension decode and check that address. The pool already threads `extensionData` through to every hook call unchanged.
2. **Separate router allowlist**: Distinguish between "router is allowed to relay swaps on behalf of allowlisted users" and "all users are allowed to swap". The extension would need to verify that the `sender` (router) is a trusted relay and that the actual user (from `extensionData`) is individually allowlisted.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)      // alice is allowlisted
  admin calls setAllowedToSwap(pool, router, true)     // router allowlisted so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, zeroForOne: true, amountIn: X, ...})

  Execution path:
    router → pool.swap(recipient, ...)
    pool passes msg.sender=router as `sender` to extension
    extension checks: allowedSwapper[pool][router] == true  ✓
    swap executes — bob drains LP funds despite never being allowlisted
```

Direct pool call by bob (without router) correctly reverts:
```
  bob → pool.swap(...)
  extension checks: allowedSwapper[pool][bob] == false  → NotAllowedToSwap ✓
```

The bypass is reachable by any unprivileged user the moment the router is added to the allowlist, which is the only configuration that makes the allowlist usable with the router for legitimate users.