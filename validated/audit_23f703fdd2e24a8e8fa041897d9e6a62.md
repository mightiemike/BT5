### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Making Allowlist Bypassable via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the user. The allowlist therefore gates the router, not the actual economic actor. A pool admin cannot simultaneously allow specific users to swap via the router and block non-allowlisted users from doing the same.

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The router does not forward the original user's address into the swap call in any way that the extension can observe. The extension only sees `sender = router_address`.

This creates a binary trap for the pool admin:

- **If the router is NOT allowlisted:** allowlisted users cannot use the router at all — the allowlist blocks them because `allowedSwapper[pool][router] == false`.
- **If the router IS allowlisted** (the only way to let allowlisted users use the router): `allowedSwapper[pool][router] == true`, so the check passes for **any** user who routes through the router, completely bypassing the allowlist.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional participants, or addresses protected from toxic flow) loses that protection entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and swap in the restricted pool. LPs who deposited under the assumption that only vetted counterparties could trade against them are exposed to unintended toxic flow, leading to LP value loss. The core access-control invariant — that only allowlisted addresses may swap — is broken.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router, at which point the bypass is immediately active for all users. The trigger requires no special permissions — any address can call the router.

### Recommendation

The pool should pass the **original user's address** through the swap call so extensions can gate on it. Two approaches:

1. Add an optional `originator` field to the swap call that the router populates with `msg.sender` before calling the pool. The pool forwards it to extensions alongside `sender`.
2. Alternatively, `SwapAllowlistExtension` should check `sender` only when `sender` is not a known router/intermediary, and require routers to attest the originator via `extensionData`. The extension would decode the originator from `extensionData` when `sender` is a registered router.

The simplest safe fix is to have the router encode the original user address into `extensionData` and have `SwapAllowlistExtension` decode and check it when present, falling back to `sender` otherwise.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is KYC'd)
  - allowedSwapper[pool][router] = true  (router allowlisted so alice can use it)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...) → msg.sender at pool = router
  - pool calls _beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] == true → PASSES
  - bob's swap executes against the restricted pool

Result:
  - bob bypasses the allowlist entirely
  - LPs are exposed to an unintended counterparty
``` [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
