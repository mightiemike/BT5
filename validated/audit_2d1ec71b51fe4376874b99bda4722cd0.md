### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Allowlist - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. If the pool admin allowlists the router (the natural step to enable router-based swaps on a curated pool), every user — including those not individually allowlisted — can bypass the per-user allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

At the pool level `msg.sender` is the **router**, so `sender` delivered to the extension is the router address, not the end user. The same applies to `exactInput` and `exactOutput` multi-hop paths. [5](#0-4) 

A pool admin who wants allowlisted users to be able to swap through the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` satisfies the guard for **every** caller of the router, regardless of whether that caller is individually allowlisted.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The disallowed user receives pool output tokens and the pool receives input tokens — a fully settled swap that the allowlist was supposed to block. This is a direct policy bypass with fund-flow consequences: the pool's LP providers accepted liquidity terms under the assumption that only vetted counterparties would trade against them.

### Likelihood Explanation

The likelihood is high. The router is the canonical, documented periphery entry point. A pool admin who configures a swap allowlist and also wants users to be able to use the router will inevitably allowlist the router address. There is no warning in the extension or its interface that doing so opens the pool to all router users. The admin has no other mechanism to simultaneously allow router access and enforce per-user identity checks.

### Recommendation

The `sender` field passed to `beforeSwap` must represent the economic actor (the end user), not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the originating `msg.sender` into `extensionData` so extensions can read the true user identity.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode and verify the true user from `extensionData` when `sender` is a known router, or the pool/router architecture should provide a dedicated "originator" field in the hook signature.

Until fixed, pool admins should be warned never to allowlist the router address on a curated pool, and should only allow direct `pool.swap()` calls from individually allowlisted addresses.

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can swap via the router.
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `extension.beforeSwap(router, recipient, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Swap executes; attacker receives output tokens. The per-user allowlist is fully bypassed. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```
