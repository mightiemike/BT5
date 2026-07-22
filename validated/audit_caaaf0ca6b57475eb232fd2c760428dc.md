### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender = router address`. The allowlist check therefore evaluates `allowedSwapper[pool][router]` rather than the actual end-user's address. A pool admin who wants to allow any allowlisted user to use the router must allowlist the router itself — which then grants every user unrestricted swap access, completely defeating the allowlist.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool.swap(...)
                   → _beforeSwap(msg.sender, ...)             // sender = router
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                             → allowedSwapper[pool][router]   // checks router, not user
```

**Pool `swap` passes `msg.sender` as `sender` to `_beforeSwap`:** [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]`:** [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly, making the router the pool's `msg.sender`:** [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the router is the pool's `msg.sender`. [5](#0-4) 

---

### Impact Explanation

The pool admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Allowlist individual users only (not the router) | Allowlisted users **cannot** swap through the router (extension sees router, not user → revert) |
| Allowlist the router to enable router-mediated swaps | **Every** user can bypass the allowlist entirely |

There is no configuration that allows specific users to use the router while blocking others. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via `MetricOmmSimpleRouter` has its access control completely broken. Unauthorized users can swap against the pool at oracle-derived prices, extracting value from LPs or manipulating pool state in ways the admin intended to prevent.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface in the periphery.
- Any pool that combines `SwapAllowlistExtension` with the router — the expected production configuration — is affected.
- No special privileges or unusual conditions are required; any unprivileged user can call the router.
- The bypass is automatic and unconditional once the router is allowlisted.

---

### Recommendation

The extension must check the actual end-user identity, not the intermediary. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router or a signed payload.

2. **Check `sender` only when called directly; require routers to forward user identity**: Define a standard where periphery contracts encode the originating user in `extensionData`, and the extension reads from there when `sender` is a known router.

The simplest safe fix is for `SwapAllowlistExtension` to read the actual swapper from `extensionData` when `sender` is not directly an EOA, or for the router to forward `msg.sender` in `extensionData` so the extension can verify the real user.

---

### Proof of Concept

```solidity
// Pool deployed with SwapAllowlistExtension.
// Admin allowlists alice (individual user) and the router (to allow router usage).
swapAllowlist.setAllowedToSwap(pool, alice, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true); // required for any router swap

// Now bob (not allowlisted) swaps through the router:
// pool.swap(msg.sender=router) → beforeSwap(sender=router) → allowedSwapper[pool][router] = true → PASSES
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Bob's swap succeeds despite not being on the allowlist.
```

The allowlist guard is silently bypassed for all router-mediated swaps once the router is allowlisted, which is the only way to allow any router usage at all.

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
