The call chain is fully traceable and the vulnerability is real. Here is the complete analysis:

---

### Title
SwapAllowlistExtension checks router address instead of actual user — allowlist bypassed via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. That `sender` is `msg.sender` from the pool's perspective — the **immediate caller of `MetricOmmPool.swap`**. When a user routes through `MetricOmmSimpleRouter`, the immediate caller is the router contract, not the user. The allowlist therefore checks the router's address, not the actual user's address, making the gate meaningless for any pool that also allowlists the router.

### Finding Description

**Call chain — direct swap:**
```
User → MetricOmmPool.swap()
         msg.sender = User
         _beforeSwap(sender=User, ...)
         SwapAllowlistExtension.beforeSwap(sender=User)
         → checks allowedSwapper[pool][User]  ✓ correct
```

**Call chain — router swap:**
```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)
              msg.sender = Router
              _beforeSwap(sender=Router, ...)
              SwapAllowlistExtension.beforeSwap(sender=Router)
              → checks allowedSwapper[pool][Router]  ✗ wrong identity
```

In `MetricOmmPool.swap`, `msg.sender` is passed directly as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

In `exactInputSingle`, the router calls `pool.swap(...)` directly — so the pool sees `msg.sender = router`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

- **Option A — allowlist the router:** Any user (including non-allowlisted ones) can call `MetricOmmSimpleRouter.exactInputSingle` and the hook passes because `allowedSwapper[pool][router] = true`. The allowlist is fully bypassed for all router-mediated swaps.
- **Option B — do not allowlist the router:** Legitimate allowlisted users cannot use the official periphery router at all; their router calls revert with `NotAllowedToSwap`.

In Option A, a disallowed user can trade on a curated/permissioned pool, violating the pool's access-control invariant. This constitutes broken core pool functionality and a curation failure — disallowed users can still trade.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery entrypoint. Any pool admin who wants their allowlisted users to use the router must allowlist the router address, which immediately opens the bypass to everyone. The path requires no special privileges, no oracle manipulation, and no non-standard tokens — just calling the public router.

### Recommendation

The `sender` passed to extension hooks must represent the **originating user**, not the immediate pool caller. Two approaches:

1. **Pass-through originator in the router:** Have the router encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension` decode it. This requires a convention between router and extension.
2. **Preferred — use `tx.origin` or a trusted-forwarder pattern in the extension:** `SwapAllowlistExtension.beforeSwap` could check `allowedSwapper[msg.sender][tx.origin]` when `sender` is a known trusted router, or the router could pass the real user address through a dedicated field.
3. **Cleanest fix:** Add an `originator` field to the hook signature (alongside `sender`) that the pool populates from a trusted source, or have the router pass the real user address as part of `extensionData` with a well-defined ABI that `SwapAllowlistExtension` reads.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router is allowed (so alice can use it)
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) — msg.sender = router
  3. _beforeSwap(sender=router, ...) is dispatched
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. bob's swap executes successfully despite not being on the allowlist

Assert: bob received output tokens from a pool he should have been barred from.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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
