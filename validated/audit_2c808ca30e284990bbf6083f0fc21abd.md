### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Making the Allowlist Bypassable for Router-Mediated Swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. If the pool admin allowlists the router (a necessary step for allowlisted users to use the router), any unprivileged user can bypass the swap allowlist entirely by routing through the router.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` identity check:** [1](#0-0) 

The extension checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument passed by the pool.

**`MetricOmmPool.swap` passes `msg.sender` as `sender`:** [2](#0-1) 

The pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`.

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` as the router:** [3](#0-2) 

When a user calls `router.exactInputSingle(...)`, the router calls `pool.swap(...)` with `msg.sender = router`. The pool then passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The pool admin faces an impossible choice:**

| Router allowlisted? | Allowlisted users can use router? | Non-allowlisted users can bypass? |
|---|---|---|
| No | ❌ Broken | ✅ Blocked |
| Yes | ✅ Works | ❌ **Bypassed** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. The extension cannot distinguish "router called by allowlisted user" from "router called by non-allowlisted user."

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for access control (e.g., regulatory compliance, curated counterparty sets, or adverse-selection mitigation) is rendered ineffective for router-mediated swaps. Once the pool admin allowlists the router to enable their allowlisted users to use the standard periphery path, any unprivileged user can call `router.exactInputSingle(pool, ...)` and trade on the restricted pool. In an oracle-anchored AMM, unrestricted counterparties can extract LP value through informed trading that the allowlist was designed to prevent, causing direct loss of LP principal.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing swap entry point. Any pool admin who wants allowlisted users to have a normal UX must allowlist the router. This is a routine operational step, not an exotic configuration. Once taken, the bypass is immediately available to any unprivileged address with no further preconditions.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economically relevant actor** — the end user — not the intermediary router. Two approaches:

1. **Extension-data signature:** Require the actual user to sign a permit that the extension verifies from `extensionData`. The router would forward the user-supplied `extensionData` unchanged (it already does via `params.extensionData`).
2. **Router-aware sender forwarding:** Introduce a trusted-router registry in the extension; when `sender` is a known router, decode the actual user from `extensionData` and check that address instead.

The `DepositAllowlistExtension` correctly gates `owner` (the position holder) rather than `sender` (the payer), which is the right model for the deposit path. The swap path needs an equivalent fix.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls extension.setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  pool admin calls extension.setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...) with msg.sender = router
    → pool calls _beforeSwap(sender = router, ...)
    → extension checks allowedSwapper[pool][router] → TRUE
    → swap executes for bob, allowlist bypassed

Direct swap (bob, not allowlisted):
  bob calls pool.swap(...) directly
    → pool calls _beforeSwap(sender = bob, ...)
    → extension checks allowedSwapper[pool][bob] → FALSE → revert ✓
```

The bypass is reachable by any unprivileged user through the public `MetricOmmSimpleRouter` entry point whenever the pool admin has allowlisted the router. [4](#0-3) [5](#0-4) [3](#0-2)

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
