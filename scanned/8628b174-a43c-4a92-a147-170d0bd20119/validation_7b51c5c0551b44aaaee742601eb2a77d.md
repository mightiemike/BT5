### Title
`SwapAllowlistExtension` checks the router's address as `sender` instead of the originating user, allowing any unprivileged caller to bypass the per-pool swap allowlist by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of the pool is the **router**, not the originating user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for their allowlisted users), every unprivileged user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
         → IMetricOmmPoolActions(params.pool).swap(recipient, ..., extensionData)
              msg.sender = router
         → MetricOmmPool.swap()
              _beforeSwap(msg.sender=router, recipient, ...)
         → ExtensionCalling._beforeSwap(sender=router, ...)
              abi.encodeCall(IMetricOmmExtensions.beforeSwap, (router, ...))
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, `msg.sender` is passed as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards it verbatim as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

When the pool admin allowlists the router address (so that their allowlisted users can use the router), the check `allowedSwapper[pool][router] == true` passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted.

The router calls the pool with `msg.sender = router` for every user: [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institutional-only, or regulatory-restricted) and configures `SwapAllowlistExtension` to gate swaps by individual user address loses the entire access-control guarantee the moment they also allowlist the router. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the curated pool and the extension will pass because it sees `sender = router`. The pool's curation policy is completely nullified. This is a direct admin-boundary break: an unprivileged path (the public router) bypasses a configured pool-level access control, matching the contest's "Admin-boundary break" and "Allowlist path" allowed-impact categories.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is a natural and expected operational step: without it, allowlisted users cannot use the router at all (their swaps revert because `allowedSwapper[pool][router] == false`). Any admin who wants to offer router UX to their allowlisted users will take this step, unknowingly opening the bypass to all users. The router is a public, permissionless contract, so once it is allowlisted the bypass is immediately available to every address on-chain.

---

### Recommendation

The extension must gate on the **originating user**, not the immediate pool caller. Two complementary fixes:

1. **Pass the original user through the router.** The router should forward `msg.sender` as `extensionData` (or a dedicated field), and the extension should decode and check that address instead of `sender`.

2. **Check `sender` only when `sender` is not a known router.** The extension can maintain a registry of trusted routers and, when `sender` is a router, require the extension data to carry a signed or verified original-user address.

The simplest production fix is to have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a recognized router address.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is KYC'd
  allowedSwapper[pool][router] = true   // admin allowlists router so alice can use it
  allowedSwapper[pool][eve]    = false  // eve is NOT allowlisted

Attack:
  eve calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → pool.swap(msg.sender=router, ...)
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router)
         allowedSwapper[pool][router] == true  ✓  (passes)
    → swap executes for eve

Result:
  eve swaps on a pool she is not allowlisted for.
  The SwapAllowlistExtension guard is completely bypassed.
``` [3](#0-2) [1](#0-0) [5](#0-4)

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
