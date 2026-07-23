### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When users swap through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router to enable router-mediated swaps, every unpermissioned user can bypass the allowlist entirely by routing through it.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, ..., extensionData)
              [pool's msg.sender = router]
         → MetricOmmPool._beforeSwap(msg.sender=router, recipient, ...)
         → ExtensionCalling._callExtensionsInOrder(BEFORE_SWAP_ORDER, abi.encodeCall(beforeSwap, (router, ...)))
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks: allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the user entered through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original caller's identity: [4](#0-3) 

The router stores the original `msg.sender` only in transient callback context for payment settlement, never in the pool call arguments: [5](#0-4) 

---

### Impact Explanation

Two concrete fund-impacting outcomes:

1. **Allowlist bypass (High):** A pool admin who wants to allow router-mediated swaps must add the router to `allowedSwapper`. Once the router is allowlisted, **any** address — including those the admin explicitly excluded — can call `router.exactInputSingle()` and pass the allowlist check, because the extension sees `sender = router`. The curated pool's access control is completely nullified for all router-mediated swaps.

2. **Broken core swap functionality (Medium):** If the admin does *not* allowlist the router, then every legitimately allowlisted user who tries to swap through the router is rejected (`NotAllowedToSwap`), making the primary swap interface unusable for the pool's intended participants.

Both outcomes directly affect user principal and core pool functionality above Sherlock thresholds.

---

### Likelihood Explanation

- The router (`MetricOmmSimpleRouter`) is the protocol's primary, documented swap entrypoint.
- Pool admins who deploy a `SwapAllowlistExtension` pool will naturally need to support router swaps; adding the router to the allowlist is the obvious (and broken) solution.
- No special privilege or unusual setup is required — any user can call `exactInputSingle` on any pool.
- The bypass is deterministic and requires zero preconditions beyond the router being allowlisted.

---

### Recommendation

The extension must gate on the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original caller through the pool:** Add an `originator` field to the pool's `swap()` signature (or a separate trusted-forwarder pattern) so the extension can check the real user.

2. **Extension-side fix (simpler):** In `SwapAllowlistExtension.beforeSwap`, if `sender` is a known trusted router, decode the real caller from `extensionData` (supplied by the router) and check that address instead. The router must commit the original `msg.sender` into `extensionData` before calling the pool.

Either way, the invariant must be: **the identity checked by the allowlist is the address that economically initiates and benefits from the swap**, not the routing contract that relays it.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the only permitted swapper)
  - allowedSwapper[pool][router] = true  (admin adds router to support router swaps)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient=bob, ...)
  - pool passes msg.sender=router to _beforeSwap
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - bob's swap executes successfully despite not being on the allowlist

Result:
  - The SwapAllowlistExtension provides zero protection against non-allowlisted users
    who route through the router.
  - Any user can trade on a "curated" pool, bypassing the intended access control.
``` [6](#0-5) [2](#0-1) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
