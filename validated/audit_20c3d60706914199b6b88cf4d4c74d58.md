### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end-user. If the router is allowlisted for a pool (the only way to let users swap via the router), every user—including those explicitly excluded from the allowlist—can bypass the gate by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The router never forwards the original caller's identity. The pool receives `msg.sender = router`, so `sender` in `beforeSwap` is the router address, not the end-user.

### Impact Explanation

A pool admin who wants to allow legitimate users to swap via the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any address**—including addresses the admin explicitly excluded—can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the allowlist check passes because `sender == router` is allowlisted. The allowlist is completely neutralized for all router-mediated swaps. LP funds in restricted pools are exposed to unauthorized traders, breaking the core access-control invariant the extension was deployed to enforce.

### Likelihood Explanation

The router is a public, permissionless contract. Any user can call it. The pool admin is forced into a binary choice: either allowlist the router (defeating the allowlist for all users) or block the router (making the router unusable for all users, including legitimate ones). There is no configuration that simultaneously allows legitimate users to use the router and blocks unauthorized users. The trigger requires no special privilege and is reachable on every router-mediated swap.

### Recommendation

The pool should pass the original end-user identity through the extension call chain. One approach: add an `originator` field to the `beforeSwap` hook arguments that the pool populates from a trusted periphery-supplied value (e.g., via `extensionData` decoded under a strict schema), or require the router to pass the real caller as `recipient` and have the allowlist check `recipient` instead of `sender`. Alternatively, `SwapAllowlistExtension` should check `recipient` (the economic beneficiary) rather than `sender` (the immediate pool caller), or the router should be redesigned to call the pool with the end-user as `msg.sender` via a delegatecall pattern.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // necessary for router users
  admin calls setAllowedToSwap(pool, alice, false)   // alice is explicitly blocked

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    -> router calls pool.swap(recipient=alice, ...)
    -> pool calls _beforeSwap(sender=router, ...)
    -> SwapAllowlistExtension.beforeSwap(sender=router, ...)
       allowedSwapper[pool][router] == true  -> check passes
    -> swap executes; alice receives output tokens

Result:
  alice, who was explicitly excluded from the allowlist, successfully swaps.
  The allowlist provides zero protection for any router-mediated swap.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
