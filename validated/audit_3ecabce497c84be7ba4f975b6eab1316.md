### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every non-allowlisted user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and uses it as the identity to gate: [1](#0-0) 

`msg.sender` inside the extension is the pool (the caller of the hook), and `sender` is whatever the pool forwarded as its first argument. The pool always forwards its own `msg.sender` — the direct caller of `pool.swap()` — as `sender`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`: [3](#0-2) 

So the pool's `msg.sender` is the router, and `sender` forwarded to the extension is the **router address**. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an impossible choice:

| Admin configuration | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert for everyone, including allowlisted users |
| **Allowlist the router** | Any user — allowlisted or not — can bypass the gate by routing through the router |

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to be a permissioned pool — e.g., KYC-gated, institutional-only, or restricted to a specific counterparty set. Once the router is allowlisted (the natural configuration for any pool that wants to support the standard periphery), the allowlist provides zero protection. Any unprivileged user can:

1. Call `router.exactInputSingle` or `router.exactInput` targeting the restricted pool.
2. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
3. The extension checks `allowedSwapper[pool][router]` → `true`.
4. The swap executes, draining LP assets at the oracle price.

This is a direct loss of LP principal: unauthorized traders can extract value from a pool that was explicitly configured to block them. It also breaks the core pool functionality the allowlist extension was deployed to enforce.

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production extension in `metric-periphery`, deployed by pool admins who want access control.
- Any pool admin who also wants to support the standard `MetricOmmSimpleRouter` (the primary user-facing entry point) must allowlist the router — triggering the bypass.
- No special privilege, flash loan, or oracle manipulation is required. Any EOA can call the router.
- The attacker controls only the standard router call; no malicious setup is needed.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end user — not the intermediary. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` for each hop, and the extension decodes and checks that address instead of `sender`.
2. **Check `sender` only for direct pool calls; decode user from `extensionData` for router calls**: The extension inspects whether `sender` is a known router and, if so, reads the real user from `extensionData`.

The simplest correct fix is for the router to always include the originating user in `extensionData` and for `SwapAllowlistExtension` to decode and check that value when present, falling back to `sender` for direct calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (executed by bob, who is NOT allowlisted):
  1. bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true  ✓
  5. Swap executes; bob receives output tokens from the restricted pool.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds — allowlist fully bypassed
``` [4](#0-3) [3](#0-2) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
