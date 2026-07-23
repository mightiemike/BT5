### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The allowlist therefore gates the router address, not the actual swapper. If the router is allowlisted (the only way to permit router-mediated swaps), every user on the network can bypass the allowlist by calling the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap`: [4](#0-3) 

So the pool receives `msg.sender = router`, passes `sender = router` to the extension, and the allowlist lookup becomes `allowedSwapper[pool][router]`. The actual end user's address is never consulted.

The same router-mediated path exists for `exactInput` (multi-hop) and `exactOutput`/`exactOutputSingle`: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, institutional market makers, or whitelisted bots). The guard is the only on-chain enforcement of that restriction.

Two fund-impacting outcomes follow:

1. **Allowlist bypass (primary)**: The admin must allowlist the router address to permit any router-mediated swap. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that arrives through the router, regardless of who the actual end user is. Any unprivileged address can call `exactInputSingle` or `exactInput` and swap against LP funds that were intended to be accessible only to approved counterparties.

2. **Allowlisted users locked out of the router (secondary)**: If the admin does not allowlist the router (to avoid the bypass above), every allowlisted user who tries to swap through the router is rejected, breaking the primary user-facing swap path.

In either case the guard fails to enforce the invariant it was deployed to protect: LP principal is exposed to unauthorized swap flow, or the pool becomes unusable for legitimate users.

---

### Likelihood Explanation

- `SwapAllowlistExtension` is a production periphery contract, not a mock.
- `MetricOmmSimpleRouter` is the primary user-facing swap entry point; virtually all non-technical users and integrators route through it.
- The admin has no way to allowlist "the router on behalf of user X" — the mapping is keyed by a single address, so allowlisting the router is an all-or-nothing decision.
- No additional privilege or special setup is required for the attacker beyond calling the public router.

---

### Recommendation

Replace the `sender` check with a check on the **economically relevant actor**. Two options:

**Option A – Check `sender` but require the router to forward the originating user.** Add a `recipient`-or-`originator` field to `extensionData` that the router populates with `msg.sender`, and have the extension decode and verify that field. This requires a coordinated change to the router and extension ABI.

**Option B – Check `sender` only for direct pool calls; for router calls, check the payer stored in transient context.** The router already stores the payer in transient storage (`_getPayer()`); expose it via a standard interface so extensions can read the true originator.

**Option C (minimal) – Document that `SwapAllowlistExtension` gates the direct caller, not the end user, and provide a separate `RouterSwapAllowlistExtension` that decodes the originator from `extensionData`.** This is the lowest-risk change but requires pool admins to choose the correct extension.

In all cases, the allowlist key must be the address that controls the economic decision to swap, not the intermediary contract that relays the call.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only approved swapper
  allowedSwapper[pool][router] = true  // admin must set this for router to work

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
        pool:      pool,
        recipient: bob,
        zeroForOne: true,
        amountIn:  X,
        ...
    })

  router calls pool.swap(bob, true, X, ...)
    → pool: msg.sender = router
    → _beforeSwap(sender=router, recipient=bob, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → check: allowedSwapper[pool][router] == true  ✓
    → swap executes, bob receives token1 from LP funds

Result:
  bob, who is not in the allowlist, successfully swaps against LP funds.
  The allowlist invariant is broken.
``` [3](#0-2) [1](#0-0) [4](#0-3)

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
