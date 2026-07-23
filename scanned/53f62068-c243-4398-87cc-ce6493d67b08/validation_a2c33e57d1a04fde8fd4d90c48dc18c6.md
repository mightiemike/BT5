### Title
`SwapAllowlistExtension` checks the router address instead of the real swapper, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the first argument forwarded by the pool — always `msg.sender` of the pool's `swap` call. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the gate for every non-allowlisted user, because the hook never sees the real swapper's address.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that first argument (`sender`) against the per-pool allowlist, using `msg.sender` (the pool) as the mapping key: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The pool admin faces an inescapable dilemma:

- **Do not allowlist the router** → `allowedSwapper[pool][router]` is `false`; every router-mediated swap reverts with `NotAllowedToSwap`, even for users who are individually allowlisted. The router is unusable on the curated pool.
- **Allowlist the router** → `allowedSwapper[pool][router]` is `true`; the hook passes for every caller regardless of their individual allowlist status, because the hook only ever sees the router's address.

There is no third option: the extension has no mechanism to inspect the original `msg.sender` of the router call, and `extensionData` is not used by `SwapAllowlistExtension`.

### Impact Explanation

Any non-allowlisted user can trade on a pool that the admin intended to restrict to a curated set of swappers. This breaks the core access-control invariant of the extension and constitutes an admin-boundary break: an unprivileged path (routing through the public router) bypasses the pool admin's configured allowlist. Depending on the pool's purpose (e.g., restricting swaps to known market makers to protect LP returns), LP providers can suffer adverse execution from unrestricted counterparties that the allowlist was designed to exclude.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing entry point documented and deployed alongside the protocol. Any pool that uses `SwapAllowlistExtension` and also wants to support the router must allowlist it, triggering the bypass. The attacker needs no special privileges — only the ability to call the public router.

### Recommendation

The extension must gate on the **real end-user**, not on the intermediary contract. Two sound approaches:

1. **Pass the original caller through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it, combined with an `onlyPool` check so the pool cannot be spoofed.
2. **Check `sender` and fall back to `recipient`**: Alternatively, redesign the hook to check the `recipient` field (which the router sets to the actual beneficiary) when `sender` is a known router, though this requires a registry of trusted routers.

The simplest safe fix is option 1: the router encodes the real user address in `extensionData`, and the extension verifies both that `msg.sender` is a registered pool and that the decoded address is allowlisted.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowlisted swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary for Alice to use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. `_beforeSwap(router, ...)` is dispatched; the extension evaluates `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite never being allowlisted.

Direct call check (confirming the bypass is router-specific):

- Bob calls `pool.swap(...)` directly → `_beforeSwap(bob, ...)` → `allowedSwapper[pool][bob]` → `false` → reverts with `NotAllowedToSwap`. ✓

The bypass is exclusively reachable through the router, exactly mirroring the ENS M-01 pattern where approval granted in one context (the router) over-extends into a context the approver did not intend to open (unrestricted swap access for all users).

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
