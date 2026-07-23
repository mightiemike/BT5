### Title
SwapAllowlistExtension checks router address instead of actual swapper when trades are routed through MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `MetricOmmPool.swap`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router contract**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`, making the allowlist gate the wrong actor. If the router is allowlisted (required for any router-mediated swap to work), every user bypasses the allowlist. If the router is not allowlisted, every individually-allowlisted user is blocked from using the router.

---

### Finding Description

**Call chain:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool.swap()
                   → _beforeSwap(msg.sender, ...)             // sender = router
                        → ExtensionCalling._beforeSwap(sender=router, ...)
                             → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                                  → allowedSwapper[pool][router]  ← WRONG ACTOR
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When the router calls the pool, the pool's `msg.sender` is the router address: [4](#0-3) 

The router stores the actual user (`msg.sender`) only in transient storage for the payment callback — it is never surfaced to the extension hook: [5](#0-4) 

---

### Impact Explanation

**Scenario A — router is allowlisted (necessary for router-mediated swaps to work at all):**
Every user, including those explicitly blocked by the pool admin, can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The curated pool's access control is completely defeated. Any non-KYC'd or otherwise restricted address can trade freely.

**Scenario B — router is not allowlisted:**
Every individually-allowlisted user is silently blocked from using the router. Their direct-pool calls succeed, but the standard periphery path reverts with `NotAllowedToSwap`. This breaks the core swap flow for legitimate users on curated pools.

Both outcomes represent a direct loss of the pool's intended access-control invariant. Scenario A is the higher-severity path: an attacker needs only to call the public router to trade on a pool that was designed to exclude them.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user who wants to bypass a curated pool's allowlist needs only to call the router instead of the pool directly — no special privileges, no flash loans, no multi-step setup. The router is a public, permissionless contract. Likelihood is **High**.

---

### Recommendation

The `sender` identity that the extension receives must be the economically relevant actor — the address that initiated the trade and will pay for it — not the intermediate router contract.

Two complementary fixes:

1. **Extension-side:** `SwapAllowlistExtension` should accept the real user identity through `extensionData` (signed or forwarded by the router) and verify it, rather than trusting the raw `sender` parameter when the caller is a known router.

2. **Router-side:** `MetricOmmSimpleRouter` should encode the real user (`msg.sender`) into `extensionData` before forwarding to the pool, so allowlist extensions can read and verify it.

A longer-term fix is for the pool to expose a dedicated "originator" field (analogous to how the router already stores the real payer in transient storage for the payment callback) and pass it through the extension hook alongside `sender`.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully despite being explicitly excluded from the allowlist.

Alternatively, if the admin does **not** allowlist the router:

4. Alice (allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The extension evaluates `allowedSwapper[pool][router] == false` → reverts with `NotAllowedToSwap`.
6. Alice cannot use the standard periphery path even though she is individually allowlisted.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
