### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks whether the **router** is allowlisted — not the actual end user. Any user can bypass a per-user swap allowlist by routing through the public router if the router address is allowlisted.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the router contract, not the end user: [4](#0-3) 

The actual end user's address is stored only in the router's transient callback context (`_getPayer()`) and is never surfaced to the pool or the extension. The extension therefore evaluates `allowedSwapper[pool][router]` for every router-mediated swap, regardless of who the real user is.

This creates an irreconcilable dilemma for any pool admin who deploys a per-user allowlist:

- **If the router is not allowlisted**: allowlisted users cannot use the router at all (broken core functionality).
- **If the router is allowlisted**: every user — including those explicitly excluded from the allowlist — can bypass the restriction by calling `router.exactInputSingle()` or `router.exactInput()`.

The same issue applies to multi-hop `exactInput` and `exactOutput` paths, where intermediate hops also call `pool.swap()` with the router as `msg.sender`.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of users (e.g., KYC-verified addresses, institutional counterparties) provides no effective access control for router-mediated swaps. Any unprivileged user can trade on the restricted pool by routing through the public `MetricOmmSimpleRouter`. This is a direct policy bypass with fund-impacting consequences: the pool receives input tokens from and delivers output tokens to unauthorized counterparties, violating the LP's and pool admin's explicit access-control intent.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any user who discovers that a pool has a swap allowlist can trivially attempt a router-mediated swap. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call suffices. The likelihood is high whenever a pool is deployed with `SwapAllowlistExtension` and the router is allowlisted.

### Recommendation

The extension must receive the real end-user identity, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool, and the extension decodes and verifies it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Add a dedicated `swapper` field to the swap interface**: The pool exposes the original payer/swapper as a separate argument to `beforeSwap` (distinct from `sender`), populated by the router from its own `msg.sender` before the pool call. The extension then checks this field.

The simplest safe fix is option 1: the router encodes `abi.encode(msg.sender)` into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `extensionData` is non-empty, falling back to `sender` for direct pool calls.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router must be allowlisted for any router swap to work.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The pool calls `_beforeSwap(router, ...)`, the extension checks `allowedSwapper[pool][router] == true`, and the swap succeeds.
6. Bob receives output tokens from the restricted pool, bypassing the per-user allowlist entirely. [5](#0-4) [6](#0-5)

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
