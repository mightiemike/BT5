Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which `MetricOmmPool.swap()` sets to `msg.sender` of the pool call. When users route through `MetricOmmSimpleRouter`, `msg.sender` inside `pool.swap()` is the router contract, not the actual user. Any pool that allowlists the router to support standard router UX simultaneously opens a bypass that lets every non-allowlisted user trade freely through the router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged as `sender` to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [3](#0-2) 

At that point `msg.sender` inside `pool.swap()` is the **router**, not the user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. For any allowlisted pool to be usable through the router at all, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check passes for **every** caller who routes through it, regardless of whether that caller is individually allowlisted. The same bypass applies to `exactInput`: [4](#0-3) 

And to `exactOutputSingle`: [5](#0-4) 

No existing guard compensates for this: `SwapAllowlistExtension` has no fallback to decode an original caller from `extensionData`, and the `allowedSwapper` mapping is keyed solely on the address passed as `sender`. [6](#0-5) 

## Impact Explanation
Any non-allowlisted user can trade on a pool intended to be restricted (KYC-gated, institutional-only, compliance-restricted) by routing through `MetricOmmSimpleRouter`. The allowlist guard silently fails open for every router-mediated swap. LP funds in the restricted pool are exposed to unauthorized counterparties, and the pool's access-control guarantee is entirely voided. This constitutes a direct, unprivileged bypass of a core access-control extension with fund-impacting consequences — unauthorized parties can interact with LP inventory at oracle prices.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool operator who deploys a `SwapAllowlistExtension`-protected pool and wants to support the standard router UX must allowlist the router — at which point the bypass is immediately active for all users. No special privileges, no malicious setup, and no non-standard tokens are required. The attacker only needs to call the public router.

## Recommendation
`SwapAllowlistExtension` must check the actual user identity, not the intermediary. The cleanest fix is to have the router encode `msg.sender` into `extensionData` before calling `pool.swap`, and have the extension decode and verify that address, falling back to `sender` when no user identity is encoded (for direct pool calls). Alternatively, a dedicated trusted-forwarder pattern can be adopted where the router is never allowlisted directly; instead, it always appends the original caller for the extension to verify.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension in beforeSwap order.
2. Pool admin: setAllowedToSwap(pool, alice, true)
   — alice is the only intended swapper.
3. Pool admin: setAllowedToSwap(pool, router, true)
   — required so alice can use the router.
4. bob (not allowlisted) calls:
       router.exactInputSingle({
           pool:       pool,
           recipient:  bob,
           zeroForOne: true,
           amountIn:   X,
           ...
       })
5. Router calls pool.swap(bob, true, X, ...) with msg.sender = router.
6. Pool calls _beforeSwap(router, bob, ...).
7. Extension checks allowedSwapper[pool][router] → true.
8. bob's swap executes successfully despite not being allowlisted.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
