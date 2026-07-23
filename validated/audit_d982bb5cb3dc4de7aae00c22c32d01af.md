Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` at the pool boundary. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][originalUser]`. If the pool admin allowlists the router to enable standard periphery usage, every unprivileged user can bypass the allowlist by routing through the public router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls the pool, the pool's `msg.sender` is the router contract — the original user's identity is permanently lost at the pool boundary: [4](#0-3) 

This creates an inescapable dilemma: if the router is not allowlisted, allowlisted users cannot use the standard periphery router. If the router is allowlisted, every unprivileged user can bypass the allowlist by routing through the public router. There is no mechanism in the router to forward the original `msg.sender` to the extension.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict swaps to KYC'd addresses, institutional partners, or specific counterparties loses that protection entirely once the router is allowlisted. Any user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and execute swaps against LP positions. This allows unauthorized users to trade against LP capital, extract value through oracle-priced swaps, and violate the pool's curation invariant. LP principal is directly at risk from actors the pool admin explicitly intended to exclude. This constitutes a broken core pool functionality causing direct loss of funds and an admin-boundary break where an unprivileged path bypasses an access control the pool admin configured.

## Likelihood Explanation
The router is the standard, documented periphery entry point. Any pool admin who wants allowlisted users to be able to use the router (the expected UX) will allowlist it. The bypass is then reachable by any unprivileged user with a single public call to `MetricOmmSimpleRouter.exactInputSingle`. No special privileges, flash loans, or multi-step setup are required. The precondition (router allowlisted) is the natural and expected admin action.

## Recommendation
The extension must gate the economically relevant actor — the original user — not the intermediary. Options:
1. **Pass the original caller through the router**: The router forwards the original `msg.sender` in `extensionData`; the extension decodes and verifies it. This requires a coordinated convention between router and extension.
2. **Check `recipient` instead of `sender`**: For swap allowlists, gating on `recipient` may better capture the intended actor, though it has edge cases when recipient differs from the initiator.
3. **Document that the router must never be allowlisted** and provide a router variant that does not intermediate the identity, or require direct pool calls for curated pools.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true) to enable router-based swaps
  - Alice (not allowlisted) wants to swap

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: curatedPool, ...})
  2. Router calls pool.swap(recipient, ...) — pool sees msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; Alice's identity was never checked

Result: Alice bypasses the allowlist and swaps against LP capital on a curated pool.
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
