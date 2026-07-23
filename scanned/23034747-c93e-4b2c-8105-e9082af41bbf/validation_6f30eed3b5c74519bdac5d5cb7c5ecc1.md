### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. This means the allowlist gates the wrong identity: if the router is allowlisted, every user on the network can bypass the per-user allowlist; if the router is not allowlisted, every allowlisted user is blocked from using the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router itself `msg.sender` of that call: [4](#0-3) 

The router never forwards the original `msg.sender` to the pool or to the extension. The extension therefore receives `sender = router address` for every router-mediated swap, regardless of who the actual user is.

Two mutually exclusive failure modes result:

**Mode A – Router is allowlisted (bypass):** The pool admin must allowlist the router to permit any router-mediated swap. Once the router is allowlisted, `allowedSwapper[pool][router] == true` satisfies the check for every caller of the router, including addresses that were never individually allowlisted. The per-user allowlist is completely bypassed.

**Mode B – Router is not allowlisted (DoS):** Individually allowlisted users who call through the router are blocked because `allowedSwapper[pool][user]` is never consulted — only `allowedSwapper[pool][router]` is. Legitimate users must call `pool.swap()` directly, breaking the expected periphery flow.

### Impact Explanation

In Mode A, any unprivileged user can swap in a pool that the admin intended to restrict to a specific set of addresses. This constitutes a direct admin-boundary break: the allowlist access control is rendered ineffective by routing through a public contract. Depending on the pool's purpose (e.g., institutional-only liquidity, KYC-gated pools), this allows unauthorized parties to extract value from restricted liquidity.

In Mode B, the core swap flow through the router is broken for all allowlisted users, making the pool's primary user-facing interface unusable.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol. Any pool that deploys `SwapAllowlistExtension` to restrict access will encounter this issue the moment a user or the admin attempts to use the router. The trigger requires no special privileges — any user can call the router.

### Recommendation

The extension must check the economically relevant actor, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original initiator via `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; decode from `extensionData` for router calls:** The extension inspects whether `sender` is a known router and, if so, decodes the real initiator from `extensionData`.

The simplest safe fix is for `MetricOmmSimpleRouter` to ABI-encode the original `msg.sender` into the `extensionData` it forwards to the pool, and for `SwapAllowlistExtension` to decode and check that value when present.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary for any router swap to work.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `_beforeSwap` is called with `sender = router`.
7. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully despite never being allowlisted.

Conversely, if step 3 is omitted (router not allowlisted), Alice calling through the router is blocked at step 7 because `allowedSwapper[pool][router] == false`, even though `allowedSwapper[pool][alice] == true`.

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
