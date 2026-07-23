Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's address against the allowlist rather than the actual user's address. Any user can therefore bypass a per-user swap allowlist by calling the public, permissionless router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) [7](#0-6) 

The pool admin faces two losing options: (1) do not allowlist the router — all router-mediated swaps revert even for individually allowlisted users; (2) allowlist the router — every user on the network can bypass the per-user gate by calling the public router. Neither option preserves the intended per-user access control. There is no existing guard that recovers the true initiator's address before the allowlist check.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (KYC'd users, institutional partners, whitelisted addresses) can be freely accessed by any unprivileged user through `MetricOmmSimpleRouter`. This constitutes broken core pool functionality: the access-controlled swap invariant is structurally unenforceable for the router path, exposing LP-owned liquidity to unauthorized swap flow and potential principal loss.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract requiring no special role, token, or setup beyond having the pool address. The bypass is a single call: `router.exactInputSingle({pool: restrictedPool, ...})`. Every pool that uses `SwapAllowlistExtension` and needs to support router-mediated swaps is affected.

## Recommendation
The `sender` forwarded to extensions should represent the economic initiator of the swap, not the immediate `msg.sender` of `pool.swap()`. Two complementary fixes:

1. **In the router:** encode the original `msg.sender` (the user) in `extensionData` so extensions can recover the true initiator.
2. **In `SwapAllowlistExtension`:** decode and verify the caller-supplied identity from `extensionData`, or require direct pool calls (no router intermediary) for allowlisted pools.

The cleanest fix is for the router to encode the true user address in `extensionData` and for `SwapAllowlistExtension` to decode and verify it, similar to how Uniswap v4 uses `hookData` for caller attestation.

## Proof of Concept
```
Setup:
  pool P configured with SwapAllowlistExtension E
  admin allowlists router R: E.setAllowedToSwap(P, router, true)
  user Alice (not individually allowlisted) wants to swap

Attack:
  Alice calls router.exactInputSingle({pool: P, ...})
  → router calls P.swap(recipient, ...) with msg.sender = router
  → pool calls E.beforeSwap(router, ...)
  → E checks allowedSwapper[P][router] == true → passes
  → Alice's swap executes in the restricted pool

Result:
  Alice, who is not in the allowlist, successfully swaps in a pool
  that was intended to be restricted to specific counterparties.
  LP funds are exposed to unauthorized swap flow.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
