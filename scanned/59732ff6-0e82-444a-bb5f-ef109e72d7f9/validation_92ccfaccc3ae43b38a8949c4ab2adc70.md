### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` passed to the extension is the router address — not the actual end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every user on the network can bypass the per-user allowlist by routing through the same public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is a two-sided failure:

1. **Bypass path**: If the pool admin allowlists the router address (so that legitimate allowlisted users can use the router), the extension approves every swap that arrives through the router, regardless of who the actual end user is. Any non-allowlisted address can call `router.exactInputSingle()` and the extension sees `sender = router`, which is allowlisted, and passes.

2. **Denial path**: If the pool admin does not allowlist the router, every allowlisted user who tries to swap through the router is blocked, because the extension sees `sender = router` (not allowlisted) and reverts.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd addresses, institutional partners) can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. The unauthorized user executes real swaps against the pool's liquidity, receiving output tokens and paying input tokens at the oracle-derived price. LPs who deposited under the assumption that only vetted counterparties would trade against them are exposed to unrestricted public order flow, which can include adversarial or toxic flow that the allowlist was designed to exclude. This is a direct loss of the access-control protection that governs who may extract value from the pool.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public periphery entry point for swaps. Any user who discovers the allowlist restriction on a pool can trivially route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. The likelihood is high whenever a pool admin enables the allowlist extension and also needs to support router-mediated swaps for their legitimate users.

### Recommendation

The extension must gate the actual end user, not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router**: `MetricOmmSimpleRouter` already stores the original `msg.sender` in transient storage as the payer. The router should forward the original caller's address as part of `extensionData` or via a dedicated field so the extension can read it. The extension would then check that address against the allowlist.

2. **Alternatively, check `sender` only for direct pool calls and require the router to attest the real user**: The extension could require that when `sender` is a known router, the `extensionData` contains a signed or attested real-user address, and check that address instead.

The simplest safe fix is to have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a recognized router address.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, allowedUser, true).
  - Pool admin calls setAllowedToSwap(pool, address(router), true)
    (required so allowedUser can use the router).

Attack:
  - unauthorizedUser calls router.exactInputSingle({pool: pool, ...}).
  - Router calls pool.swap(recipient, ...) with msg.sender = router.
  - Pool calls extension.beforeSwap(sender=router, ...).
  - Extension checks allowedSwapper[pool][router] == true → passes.
  - unauthorizedUser's swap executes successfully against the pool.

Result:
  - unauthorizedUser receives output tokens from a pool that was
    supposed to be restricted to allowedUser only.
  - The allowlist invariant is broken with a single public router call.
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
