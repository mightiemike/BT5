### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address rather than the actual user's address. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Call path for a direct swap:**
```
user → pool.swap()
  pool: _beforeSwap(msg.sender=user, ...)
  extension: allowedSwapper[pool][user]  ← correct actor
```

**Call path for a router-mediated swap:**
```
user → MetricOmmSimpleRouter.exactInputSingle()
  router → pool.swap(params.recipient, ...)
  pool: _beforeSwap(msg.sender=router, ...)
  extension: allowedSwapper[pool][router]  ← wrong actor
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Allowlist only specific users (not the router) | Allowed users cannot swap via the router (DoS on the standard periphery path) |
| Allowlist the router to fix the DoS | Every user, including disallowed ones, can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows permitted users to use the router and blocks unpermitted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: the admin intends to restrict trading to a specific set of addresses. Any unpermitted user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) instead of calling `pool.swap` directly. The router is a public, permissionless contract. The bypass requires no special privileges, no malicious setup, and no non-standard tokens. The result is unauthorized trading on a pool whose entire security model depends on the allowlist being enforced.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Any user who reads the periphery interface will naturally use it. The bypass is a single function call with no preconditions beyond the router being allowlisted (which the admin must do to allow any router-mediated swap). Likelihood is high.

---

### Recommendation

The extension must check the original user, not the intermediary. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to be excluded from the allowlist and instead check a user-supplied identity from `extensionData`**: Formalize a `SwapAllowlistExtensionData` struct that the router always populates with the original caller.

The minimal fix is to document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and must only be used with direct `pool.swap` calls, but this breaks the intended periphery integration.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension
// Admin allowlists the router so that permitted users can swap via router
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Admin does NOT allowlist attacker
// allowedSwapper[pool][attacker] == false

// Attacker bypasses the allowlist:
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    ...
    extensionData: ""
}));
// Succeeds: extension checks allowedSwapper[pool][router] == true
// Attacker has swapped on a pool they were never permitted to access
```

The `beforeSwap` hook receives `sender = address(router)`, which is allowlisted, so the check passes regardless of who the actual user is. [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
