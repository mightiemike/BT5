### Title
`SwapAllowlistExtension` Guard Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument (which the pool sets to `msg.sender` of the `swap()` call) against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (required for any router-mediated swap to succeed), the per-user allowlist is nullified for every user on that pool.

---

### Finding Description

**Root cause — identity mismatch in the hook argument:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)   // sender = pool's msg.sender
  )
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the allowlist keyed by `msg.sender` (the pool):

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**The bypass path:**

When a non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The pool's `msg.sender` is the **router**, not the end user. The extension receives `sender = router`. If the router address is in `allowedSwapper[pool]` (which the pool admin must set for any allowlisted user to use the router), the check passes for **every** caller regardless of their individual allowlist status.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all four router entry points call `pool.swap()` with `msg.sender = router`.

**The dilemma is inescapable:**
- Router allowlisted → every user bypasses the per-user guard
- Router not allowlisted → allowlisted users cannot use the router at all (broken functionality)

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to enforce KYC, institutional-only access, or regulatory compliance can be accessed by any arbitrary address by routing through the public `MetricOmmSimpleRouter`. The allowlist guard — the only mechanism preventing unauthorized swaps — is completely nullified. Unauthorized users can drain LP value from a restricted pool at oracle-fair prices, causing direct loss of LP principal and breaking the pool's intended access model.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is allowlisted can immediately exploit this with a single `exactInputSingle` call. No special privileges, flash loans, or multi-step setup are required. The router is the standard user-facing entry point, so the allowlist admin is likely to allowlist it.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the intermediate dispatcher. Two complementary fixes:

1. **Pass the original initiator through the router.** Add a `payer`/`originator` field to the swap call or use transient storage (as the router already does for callback context) to record the original `msg.sender` before calling the pool, then have the pool forward it as a separate `originator` argument to extensions.

2. **Check `sender` in the extension against the router's stored payer.** Alternatively, `SwapAllowlistExtension` can call back into the router (if `sender` is a known router) to retrieve the original caller, though this couples the extension to the router.

The simplest correct fix is for `MetricOmmPool.swap()` to accept an explicit `originator` parameter (defaulting to `msg.sender` for direct calls) and pass that to extensions instead of `msg.sender`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   (admin must set this for router users)
  allowedSwapper[pool][alice] = true    (alice is the intended allowlisted user)
  allowedSwapper[pool][eve]   = false   (eve is NOT allowlisted)

Attack:
  eve calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(...) with msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → check passes, swap executes
    → eve receives output tokens, pool LP value is transferred to eve

Result:
  eve bypassed the allowlist and swapped on a restricted pool.
  The allowlist guard provided zero protection.
```