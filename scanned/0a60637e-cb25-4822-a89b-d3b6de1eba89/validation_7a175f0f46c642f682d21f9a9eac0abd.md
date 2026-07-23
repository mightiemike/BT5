### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address the pool received as its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the pool admin allowlists the router to support router-mediated swaps, every unprivileged address in the system can bypass the curated-pool gate by routing through the router.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

**Step 2 — The router calls `pool.swap()` directly, making itself `msg.sender` in the pool.**

`exactInputSingle` (and every other router entry point) calls `pool.swap()` without forwarding the original caller: [3](#0-2) 

The actual end-user address is stored only in transient storage for the payment callback; it is never forwarded to the pool as `sender`.

**Step 3 — The allowlist extension checks the router address, not the user.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the router) as the identity to gate: [4](#0-3) 

When the router is the caller, the effective check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

---

### Impact Explanation

Two fund-impacting outcomes follow directly:

**Outcome A — Full allowlist bypass (critical).**
A pool admin who wants to support router-mediated swaps on a curated pool must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[pool][router]` passes for every swap that arrives through the router, regardless of who the end user is. Any address — including those explicitly denied — can bypass the curated gate by calling `exactInputSingle` or any other router entry point. The pool's entire access-control invariant is broken for all router-mediated volume.

**Outcome B — Allowlist DoS for legitimate users (high).**
If the pool admin does not allowlist the router, every allowlisted user who attempts to swap through the router is rejected with `NotAllowedToSwap`, even though they are individually permitted. The router is the primary user-facing entry point; blocking it makes the pool effectively unusable for normal users.

Both outcomes are direct consequences of a single unprivileged action (calling the public router) with no admin cooperation required.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented primary entry point for swaps. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router is immediately affected. The attacker needs only to call a public router function — no special role, no setup, no token manipulation. Likelihood is high.

---

### Recommendation

The pool should pass the original end-user address as `sender` to the extension, not its own `msg.sender`. Two complementary fixes:

1. **Pool-side**: Accept an explicit `sender` parameter in `swap()` (similar to how `addLiquidity` accepts an explicit `owner`), or read the true originator from a trusted periphery registry.
2. **Extension-side (interim)**: Gate on the `recipient` field or require the router to forward the real user address in `extensionData`, and have the extension decode and verify it.

The cleanest fix mirrors the external report's resolution: derive the economically relevant actor from a trusted on-chain source rather than trusting `msg.sender` through an intermediary call chain.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice] = true   // alice is the only permitted swapper
  allowedSwapper[P][router] = true  // admin adds router to support router-mediated swaps

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: P, recipient: bob, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...)   // msg.sender in pool = router
      → _beforeSwap(sender=router, ...)
        → E.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  // check passes
      → swap executes, bob receives tokens

Result:
  bob, who is explicitly not on the allowlist, completes a swap on a curated pool.
  The allowlist provides zero protection for any router-mediated swap.
```

If the admin does not allowlist the router, the symmetric DoS applies: alice calls `router.exactInputSingle()`, the extension checks `allowedSwapper[pool][router] == false`, and the call reverts with `NotAllowedToSwap` even though alice is individually permitted.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
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
