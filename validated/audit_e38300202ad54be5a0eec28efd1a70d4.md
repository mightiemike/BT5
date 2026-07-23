Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` becomes the router address. If the router is allowlisted (a natural admin action to enable router-based swaps for allowlisted users), every unprivileged user can bypass the per-user swap allowlist entirely by routing through the router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then performs its allowlist check against that `sender` argument, where `msg.sender` is the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`. If the router is allowlisted, the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted. This is structurally inconsistent with `DepositAllowlistExtension`, which correctly gates by `owner` (the economic beneficiary), an explicit parameter invariant to whether the call comes from a direct user or a liquidity-adder contract: [5](#0-4) 

No analogous invariant exists for swaps: the pool only exposes `msg.sender` (the direct caller) to extensions, not the originating user. There is no mechanism in `SwapAllowlistExtension` to decode an originating user from `extensionData`.

## Impact Explanation
A pool admin who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties) faces an inescapable dilemma: not allowlisting the router breaks the swap flow for allowlisted users; allowlisting the router opens the pool to every unprivileged user. In the second configuration, any non-allowlisted address can execute swaps against the curated pool, bypassing the access-control invariant the extension was designed to enforce. This constitutes broken core pool functionality and direct unauthorized access to LP funds.

## Likelihood Explanation
The bypass requires the router to be allowlisted, which is a natural admin action when allowlisted users are expected to use the router. The trigger is fully unprivileged: any EOA or contract can call `MetricOmmSimpleRouter.exactInputSingle` with no special permissions. The condition is likely to occur in any production deployment where the pool admin intends to support router-based swaps for curated users.

## Recommendation
The router should encode the originating user's address into `extensionData` on every swap call, and `SwapAllowlistExtension.beforeSwap` should decode and check that address when `sender` is a known router. Alternatively, the pool interface should be extended to carry an `originator` field distinct from `msg.sender`, analogous to how `addLiquidity` separates `msg.sender` (payer) from `owner` (beneficiary). A simpler short-term mitigation is to document that allowlisting the router is equivalent to `allowAllSwappers = true` and enforce at the admin setter level that the router address cannot be added to `allowedSwapper` when individual allowlist entries exist.

## Proof of Concept
1. Pool is deployed with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
8. Bob's swap executes successfully against the curated pool, bypassing the per-user allowlist.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
