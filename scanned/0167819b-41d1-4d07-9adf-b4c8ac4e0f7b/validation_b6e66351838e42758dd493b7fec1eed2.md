### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Unauthorized Swappers Access Restricted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every user — including non-allowlisted ones — can bypass the guard by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, so the pool's `msg.sender` — and therefore the `sender` the extension sees — is the **router address**, not the originating user: [4](#0-3) 

The same substitution occurs for `exactInput` (intermediate hops use `address(this)` as payer) and `exactOutput`: [5](#0-4) 

This creates an irreconcilable dilemma for the pool admin:

- **Router not allowlisted**: every allowlisted user is silently blocked from using the router — broken core functionality.
- **Router allowlisted**: every non-allowlisted user can bypass the guard by routing through the router — the allowlist is nullified.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of counterparties (e.g., KYC'd traders, institutional partners). When the router is allowlisted to restore legitimate user access, any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`/`exactOutput`) and swap against the pool's liquidity without restriction. LP funds in a restricted pool are exposed to unauthorized counterparties, violating the pool's access-control invariant and enabling value extraction the LPs never consented to.

### Likelihood Explanation

The router is a public, permissionless contract. No special role or privilege is required. The bypass is a single direct call to `exactInputSingle`. Any user who discovers the allowlist can trivially route around it. The pool admin has no on-chain mechanism to distinguish a router call originating from an allowlisted user from one originating from a non-allowlisted user.

### Recommendation

The extension must gate the **economic actor**, not the immediate pool caller. Two complementary fixes:

1. **Pass the originating user through the router**: have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have the extension decode and check it. This requires a coordinated protocol convention.

2. **Check `sender` against the allowlist only when `sender` is not a known router, and require the router to attest the real user**: add a trusted-router registry to `SwapAllowlistExtension` so that when `sender` is a registered router, the extension reads the real user from `extensionData`.

The simplest safe default: document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and revert in `beforeSwap` when `sender` is a registered periphery router unless the router itself is the intended gated entity.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true      // alice is the only allowed swapper
  allowedSwapper[pool][router] = true     // admin must set this so alice can use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., extensionData: ""})

  Execution trace:
    router.exactInputSingle  (msg.sender = bob)
      → pool.swap(...)       (msg.sender = router)
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (guard passes)
        → swap executes, bob receives output tokens

Result:
  bob swaps against the restricted pool without being allowlisted.
  alice's LP position is traded against by an unauthorized counterparty.
``` [6](#0-5) [7](#0-6)

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
