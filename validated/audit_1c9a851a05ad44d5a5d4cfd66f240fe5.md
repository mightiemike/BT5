### Title
SwapAllowlistExtension gates the router's address instead of the end-user, making per-user swap restrictions unenforceable through MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. This makes it structurally impossible to enforce per-user swap restrictions on any pool that also supports router-mediated swaps.

---

### Finding Description

**Root cause — wrong actor bound in the hook:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Router path — the router is the pool's `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The structural trap:**

A pool admin who wants to restrict swaps to a curated set of users faces a binary, broken choice:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | **BLOCKED** (router not in list) | Blocked |
| Yes | Passes | **PASSES** (router is in list — bypass) |

There is no configuration that simultaneously allows allowlisted users to swap through the router **and** blocks non-allowlisted users from doing the same.

---

### Impact Explanation

When a pool admin allowlists the router (the only way to let curated users trade through the standard periphery), every user — including those explicitly excluded from the per-user allowlist — can call `router.exactInputSingle()` and execute swaps on the restricted pool. The allowlist guard fails open for all router-mediated swaps. This breaks the core curation invariant of the pool and allows unauthorized parties to trade against LP funds under conditions the pool admin intended to prohibit.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys a `SwapAllowlistExtension` to restrict trading to a curated set of counterparties will, in practice, also need to support router-mediated swaps for those counterparties. The moment the admin allowlists the router to enable that, the per-user restriction is fully bypassed. The trigger requires no special privilege — any public user can call the router.

---

### Recommendation

The extension must be able to identify the **original end user**, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. The pool's `beforeSwap` hook already forwards `extensionData` unchanged.

2. **Dedicated sender field**: Add an `originalSender` field to the `beforeSwap` hook signature that the pool populates from a transient-storage initiator set at the top of `swap`, so the extension always sees the true originator regardless of intermediary contracts.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)      // Alice is allowed
  admin calls setAllowedToSwap(pool, router, true)     // router must be allowed for Alice to use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})
      → pool.swap(msg.sender = router, ...)
        → extension.beforeSwap(sender = router, ...)
          → allowedSwapper[pool][router] == true  ✓  PASSES

Result:
  Bob executes a swap on a pool that was supposed to block him.
  The per-user allowlist is completely bypassed via the router.
``` [3](#0-2) [6](#0-5) [1](#0-0)

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
