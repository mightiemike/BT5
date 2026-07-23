Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is the pool's own `msg.sender`. When users swap through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. This means the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, completely nullifying the allowlist for all router-mediated swaps.

## Finding Description
The call chain is confirmed by production code:

**Step 1:** `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing the pool's own `msg.sender` as `sender`. [1](#0-0) 

**Step 2:** `ExtensionCalling._beforeSwap()` forwards that `sender` value verbatim to every configured extension via `_callExtensionsInOrder`. [2](#0-1) 

**Step 3:** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the user entered through the router. [3](#0-2) 

**Step 4:** `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly without forwarding the original caller's identity into the swap arguments. The original `msg.sender` is stored only in transient callback context for payment settlement, never passed to the pool as the initiating identity. [4](#0-3) 

The exact wrong value is: `allowedSwapper[pool][router]` is checked instead of `allowedSwapper[pool][user]`. No existing guard corrects this — the extension has no awareness of trusted routers and no mechanism to decode the real caller from `extensionData`.

## Impact Explanation
Two concrete fund-impacting outcomes:

1. **Allowlist bypass (High):** A pool admin who allowlists the router to support router-mediated swaps inadvertently grants every unpermissioned address the ability to swap on the curated pool. Any user calling `router.exactInputSingle()` passes the check because `allowedSwapper[pool][router] = true`. The access control invariant of the pool is completely nullified.

2. **Broken core swap functionality (Medium):** If the admin does *not* allowlist the router, every legitimately allowlisted user who tries to swap through the router is rejected with `NotAllowedToSwap`, making the primary swap interface unusable for the pool's intended participants.

Both outcomes directly affect user principal and core pool functionality.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the protocol's primary documented swap entrypoint. Pool admins deploying a `SwapAllowlistExtension` pool will naturally need to support router swaps; adding the router to the allowlist is the obvious and broken solution. No special privilege or unusual setup is required — any user can call `exactInputSingle` on any pool. The bypass is deterministic and requires zero preconditions beyond the router being allowlisted.

## Recommendation
The extension must gate on the original user, not the intermediary. Two complementary fixes:

1. **Router-side fix:** In `MetricOmmSimpleRouter.exactInputSingle`, encode the original `msg.sender` into `extensionData` before calling `pool.swap()`, so the extension can decode and check the real caller.

2. **Extension-side fix:** In `SwapAllowlistExtension.beforeSwap`, if `sender` is a known trusted router, decode the real caller from `extensionData` and check `allowedSwapper[pool][realCaller]` instead.

3. **Protocol-level fix:** Add an `originator` field to the pool's `swap()` signature so the extension always receives the economically initiating address regardless of routing path.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the only permitted swapper)
  - allowedSwapper[pool][router] = true  (admin adds router to support router swaps)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient=bob, ...)  [pool's msg.sender = router]
  - pool calls _beforeSwap(msg.sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - bob's swap executes successfully despite not being on the allowlist

Result:
  - SwapAllowlistExtension provides zero protection against non-allowlisted users
    routing through the router.
  - Any address can trade on a "curated" pool by calling exactInputSingle.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
