### Title
`SwapAllowlistExtension` Gates on Router Address Instead of User — Allowlist Fully Bypassed via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the user. The allowlist therefore gates on the router's address, not the actual trader's address. Any user can bypass a per-user swap allowlist by routing through the public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         └─ pool.swap(recipient, ...)          // msg.sender = router
               └─ _beforeSwap(msg.sender, ...) // sender = router
                     └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                           └─ allowedSwapper[pool][router]  ← checked, NOT user
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap = router, not end-user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that identity against the allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

**Two failure modes arise:**

1. **Allowlisted users are locked out of the router.** A pool admin allowlists specific user addresses. Those users call `exactInputSingle` on the router. The router calls `pool.swap`; `sender` = router ≠ any allowlisted user → revert. The pool is unusable through the standard periphery for its intended audience.

2. **Complete allowlist bypass.** To fix mode 1, the pool admin allowlists the router address (`allowedSwapper[pool][router] = true`). Now every call through the router passes the check regardless of who the end-user is. Any non-allowlisted address can call `exactInputSingle` and swap freely in a pool that was meant to be restricted.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` with `msg.sender = router`.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified traders, institutional counterparties, or whitelisted market makers) loses that restriction entirely once the router is allowlisted. Any unprivileged address can execute swaps at oracle-derived bid/ask prices against the pool's full liquidity, extracting value from LPs who deposited under the assumption that only vetted counterparties could trade. This is a direct loss of LP principal through unauthorized swap execution — a broken core pool invariant.

---

### Likelihood Explanation

The router is the standard, documented user-facing entry point for swaps. Any pool admin who discovers that allowlisted users cannot swap through the router will naturally allowlist the router to restore usability, unknowingly opening the bypass. The trigger requires no special privilege: any address can call `MetricOmmSimpleRouter.exactInputSingle`. Likelihood is high once the pool is live and the admin attempts to make it usable.

---

### Recommendation

Pass the economically meaningful identity — the end-user — through the hook argument rather than the direct `pool.swap` caller. Two complementary fixes:

1. **In `MetricOmmPool.swap`**: record the original `msg.sender` in transient storage before calling the pool, and expose it as a separate `originator` argument to extension hooks, or have the router forward the user address explicitly in `extensionData`.

2. **In `SwapAllowlistExtension.beforeSwap`**: gate on the `recipient` argument (already passed as the second parameter) if the design intent is to gate who receives output, or require the router to encode the real user in `extensionData` and decode it in the extension.

The simplest correct fix is for the router to encode `msg.sender` into `extensionData` and for the extension to decode and check that value, preserving the direct-pool path's existing behavior.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][router] = false // router not explicitly listed

Step 1 — alice swaps directly (works):
  alice → pool.swap(...)
  sender = alice → allowedSwapper[pool][alice] = true → passes

Step 2 — alice swaps via router (fails):
  alice → router.exactInputSingle({pool, ...})
         → pool.swap(...)   // msg.sender = router
  sender = router → allowedSwapper[pool][router] = false → REVERT

Step 3 — admin fixes by allowlisting router:
  setAllowedToSwap(pool, router, true)
  allowedSwapper[pool][router] = true

Step 4 — eve (non-allowlisted) bypasses via router:
  eve → router.exactInputSingle({pool, ...})
        → pool.swap(...)   // msg.sender = router
  sender = router → allowedSwapper[pool][router] = true → PASSES
  eve executes swap in restricted pool — allowlist fully bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
