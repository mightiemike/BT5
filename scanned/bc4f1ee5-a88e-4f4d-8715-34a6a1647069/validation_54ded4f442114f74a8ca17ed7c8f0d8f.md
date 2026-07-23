### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool passes `msg.sender` (the router) as `sender` to the extension. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those not on the allowlist — can bypass the per-user gate by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

A pool admin who wants to allow router-mediated swaps must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, regardless of whether that caller is individually permitted. The per-user gate is completely bypassed for all router-mediated swaps.

### Impact Explanation

Any user who is not on the allowlist can swap in a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). The allowlist's purpose — restricting swaps to a specific set of addresses — is nullified for the router path. This is a direct broken-core-functionality impact: the pool's curation policy fails to hold for the supported periphery entrypoint.

### Likelihood Explanation

The router is the primary user-facing swap entrypoint. A pool admin who deploys a curated pool and wants users to be able to use the standard router will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call the router.

### Recommendation

The extension must resolve the original user, not the intermediate router. Two approaches:

1. **Pass the original user through the router**: The router could forward the original `msg.sender` in `extensionData`, and the extension could decode and verify it. This requires a coordinated convention between the router and the extension.

2. **Gate on `recipient` instead of `sender`**: For swap allowlists, gating on the `recipient` (the address that receives output tokens) is a closer proxy for the economic beneficiary. The pool already passes `recipient` as the second argument to `beforeSwap`.

3. **Document the limitation explicitly**: At minimum, warn pool admins that allowlisting the router opens the gate to all router users, and that per-user enforcement is only possible for direct `pool.swap()` calls.

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowed.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router added so alice can use it.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient=bob, ...).
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes despite not being on the allowlist.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
