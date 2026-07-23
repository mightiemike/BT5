### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any router-based swap to succeed), every user gains unrestricted access to the pool regardless of their individual allowlist status.

### Finding Description

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is in the per-pool allowlist: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the **router address**: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. For the router to be usable with the pool at all, the admin must allowlist the router address. Once that entry exists, the check passes for **every** user who routes through the router, regardless of whether that user is individually permitted.

This is structurally different from `DepositAllowlistExtension`, which correctly checks the explicit `owner` parameter (the actual position beneficiary) rather than `msg.sender`: [5](#0-4) 

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to enforce KYC, compliance, or access-control policies loses all enforcement the moment the router is allowlisted. Any unprivileged address can call `router.exactInputSingle()` and execute a live swap against the pool, receiving real token output. The pool's token balances change and LP claims are affected by trades that the allowlist was supposed to block. This is a direct, fund-impacting bypass of a configured protection mechanism.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. A pool admin who wants to support normal user-facing trading would naturally allowlist the router. The admin has no on-chain signal that doing so collapses per-user enforcement — the extension's `setAllowedToSwap` API and `isAllowedToSwap` view function both suggest per-address granularity. The bypass requires only a standard router call, available to any address with no special privilege.

### Recommendation

Pass the true end-user identity through the swap path. The simplest fix is to add an explicit `swapper` parameter to `pool.swap()` (analogous to `owner` in `addLiquidity()`), have the pool pass it to `_beforeSwap()`, and have the extension check that value. Alternatively, the router can encode the originating `msg.sender` in `extensionData` and the extension can decode and verify it — but this requires the extension to trust the router's encoding, which reintroduces a trust assumption. The cleanest solution mirrors the deposit path: make the swapper identity an explicit, caller-supplied, pool-verified parameter rather than inferring it from `msg.sender`.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. `attacker` calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is `router`.
6. Pool calls `extension.beforeSwap(router, recipient, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
8. Swap executes; attacker receives output tokens. The allowlist check on `attacker` was never performed. [6](#0-5) [7](#0-6)

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
