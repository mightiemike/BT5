### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User — Any User Can Bypass a Curated Pool's Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the pool admin allowlists the router to enable router-based swaps, every unpermissioned user on the network can bypass the curated allowlist by routing through the public router.

---

### Finding Description

**Hook dispatch — `MetricOmmPool.swap`**

The pool passes `msg.sender` as the `sender` argument to every before-swap hook:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [1](#0-0) 

**Extension check — `SwapAllowlistExtension.beforeSwap`**

The extension receives that `sender` and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

**Router path — `MetricOmmSimpleRouter.exactInputSingle`**

When a user calls the router, the router calls `pool.swap()` directly:

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
``` [3](#0-2) 

The pool's `msg.sender` is the **router contract address**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The same wrong-actor binding applies to multi-hop `exactInput`** — for every hop the pool sees `msg.sender = router`: [4](#0-3) 

---

### Impact Explanation

Two concrete fund-impacting outcomes arise:

**Scenario A — Allowlist bypass (Critical)**
A pool admin deploys a curated pool (e.g., KYC-gated or institution-only) with `SwapAllowlistExtension`. To let allowlisted users trade via the router, the admin must allowlist the router address. Once the router is allowlisted, **every user on the network** can call `exactInputSingle` / `exactInput` / `exactOutput` and trade on the curated pool, because the extension only sees `sender = router` and the router is allowlisted. The curation policy is completely nullified; unauthorized users can drain LP value from a pool that was supposed to be restricted.

**Scenario B — Broken core functionality (High)**
If the admin does not allowlist the router, allowlisted users cannot use the router at all — their swaps revert with `NotAllowedToSwap` even though they are individually permitted. The only usable path is a direct `pool.swap()` call, which requires the caller to implement `metricOmmSwapCallback` themselves. The router — the primary supported periphery entrypoint — is effectively unusable for any allowlisted pool.

Both outcomes are contest-relevant: Scenario A is a direct policy bypass enabling unauthorized trading; Scenario B breaks the core swap flow for legitimate users of curated pools.

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production periphery contract explicitly designed for curated pools.
- The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint.
- Any pool that combines both — the natural and expected configuration — is immediately affected.
- No special privileges, malicious setup, or non-standard tokens are required. Any unpermissioned user can trigger Scenario A by calling the public router.
- Likelihood is **High**.

---

### Recommendation

The extension must gate the **original end user**, not the intermediate router. Two complementary fixes:

1. **Pass the real user through the router**: The router should forward the original `msg.sender` as part of `extensionData` (or a dedicated field), and the extension should decode and verify it. This requires a coordinated interface change.

2. **Check `sender` against a router-aware allowlist**: The extension can maintain a separate mapping of trusted routers and, when `sender` is a trusted router, require the real user identity to be supplied and verified inside `extensionData`.

3. **Alternatively, gate at the router level**: The router can maintain its own per-pool user allowlist and revert before calling `pool.swap()` for non-allowlisted callers. This keeps the extension simple but moves trust to the router.

The simplest safe fix is option 2 or 3, since option 1 requires a core interface change.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as extension1.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can use the router).
  - Pool admin calls setAllowedToSwap(pool, alice, true)
    (alice is the intended allowlisted user).
  - Pool admin does NOT call setAllowedToSwap(pool, eve, true)
    (eve is an unauthorized user).

Attack:
  1. Eve calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient, ...) — pool sees msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. ExtensionCalling encodes and calls SwapAllowlistExtension.beforeSwap(sender=router, ...).
  5. Extension checks allowedSwapper[pool][router] → true (admin allowlisted router).
  6. Hook returns success selector; swap executes.
  7. Eve successfully trades on a pool she was never authorized to access.

Verification:
  - Direct call: eve calls pool.swap() directly → sender=eve → allowedSwapper[pool][eve]=false → revert NotAllowedToSwap. ✓ (blocked)
  - Router call: eve calls router.exactInputSingle() → sender=router → allowedSwapper[pool][router]=true → swap succeeds. ✗ (bypass)
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
