### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the **router's address**, not the actual end-user's address. This creates an irreconcilable structural conflict: either the router is allowlisted (any user bypasses the guard) or it is not (no allowlisted user can use the router at all).

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` of every pool call: [4](#0-3) 

For multi-hop `exactInput`, every hop is called by the router: [5](#0-4) 

The result is that the allowlist never sees the actual end-user address — it sees the router address on every swap routed through the periphery.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool faces two mutually exclusive outcomes:

1. **Router not allowlisted:** Every allowlisted user who calls the router is rejected (`NotAllowedToSwap`). The router is unusable for the pool, breaking the standard swap UX.
2. **Router allowlisted** (the natural fix): The allowlist check passes for `allowedSwapper[pool][router] == true`, so **any** address — including addresses explicitly excluded from the allowlist — can swap by routing through `MetricOmmSimpleRouter`. The guard is fully bypassed.

In case 2, unauthorized users can drain LP liquidity from a pool that was intended to be restricted (e.g., KYC-gated, institutional-only, or compliance-restricted). LPs deposited under the assumption that only approved counterparties would trade against them; unauthorized swaps violate that invariant and expose LP principal to unintended counterparty risk.

---

### Likelihood Explanation

The router is the standard, documented swap entry point for end users. Any pool admin who wants allowlisted users to be able to use the router will allowlist it. The bypass is then reachable by any unprivileged address with no special setup. The admin action (allowlisting the router) is a reasonable and expected operational step, not a malicious or exotic configuration.

---

### Recommendation

The `SwapAllowlistExtension` should check the actual end-user identity rather than the immediate `msg.sender` of the pool call. Two approaches:

1. **Pass the real initiator through `extensionData`:** The router encodes `msg.sender` (the actual user) into `extensionData`; the extension decodes and checks it. This requires the router to cooperate and the extension to trust the encoding.
2. **Check `recipient` instead of (or in addition to) `sender`:** For single-hop swaps the recipient is often the actual user, though this is not reliable for multi-hop paths.
3. **Require direct pool interaction for allowlisted pools:** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call `pool.swap()` directly. This is a UX limitation but preserves the guard's integrity.

The cleanest fix is option 1 with a signed or factory-verified user identity in `extensionData`, so the extension can authenticate the real initiator regardless of the call path.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension 1)
  - Admin allowlists Alice (allowedSwapper[pool][alice] = true)
  - Admin allowlists the router (allowedSwapper[pool][router] = true)
    (necessary so Alice can use the router)

Attack:
  - Bob (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: bob, ...})
  - Router calls pool.swap(bob, ...) with msg.sender = router
  - Pool calls extension.beforeSwap(router, bob, ...)
  - Extension checks: allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes against LP liquidity despite not being allowlisted

Result:
  - Bob drains LP liquidity from a pool intended to be restricted to Alice only
  - The SwapAllowlistExtension guard is fully bypassed
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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
```
