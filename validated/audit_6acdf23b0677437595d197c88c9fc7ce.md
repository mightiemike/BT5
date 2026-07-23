Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any unprivileged caller to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router to enable router-based swaps — the natural and expected configuration — every address on the network can bypass the curated allowlist by calling any router entry point, rendering the extension's access control completely ineffective for router-mediated swaps.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as `sender` to every configured extension: [2](#0-1) 

**Step 2 — The extension checks `allowedSwapper[pool][sender]`.**

`SwapAllowlistExtension.beforeSwap` uses `sender` (the first argument, i.e., the pool's `msg.sender`) as the identity to gate: [3](#0-2) 

**Step 3 — The router calls `pool.swap()` directly, so `msg.sender` in the pool is the router.**

`exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` from within the router: [4](#0-3) 

For multi-hop `exactInput`, every hop is called from the router: [5](#0-4) 

`exactOutputSingle` and `exactOutput` follow the same pattern, calling `pool.swap(...)` directly from the router contract. [6](#0-5) 

**Result:** The extension always sees `sender = router_address` for every router-mediated swap. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. There is no mechanism in the extension or the pool to recover the originating user's address. The `extensionData` field is passed through but the extension does not decode or verify any user identity from it.

## Impact Explanation

This is a direct admin-boundary break: an unprivileged user bypasses the pool admin's explicitly configured access control policy. The `SwapAllowlistExtension` is designed to restrict swap access to a curated set of addresses (e.g., KYC-verified users, institutional counterparties). When the router is allowlisted — the only way to support the standard periphery path — the allowlist is completely neutralized for all router-mediated swaps. Any EOA can execute swaps, interact with pools they were explicitly excluded from, and drain liquidity at oracle prices. The pool admin has no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps.
- A pool admin deploying `SwapAllowlistExtension` to restrict access will naturally also allowlist the router to support standard periphery usage.
- The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can exploit it by calling `exactInputSingle` on the router.
- The misconfiguration is not obvious: the admin correctly allowlists the router believing it enables router support while preserving user-level gating, but the extension architecture makes this impossible.

## Recommendation

The extension must gate the **originating user**, not the immediate caller of the pool. Concrete options:

1. **Pass the original user through `extensionData`.** The router forwards the originating `msg.sender` inside `extensionData`; the extension decodes and verifies it. This requires a trust relationship between the extension and the router (e.g., the extension only accepts router-embedded identity when `msg.sender` of the extension call is a factory-registered router).

2. **Special-case the router as a transparent forwarder.** The extension checks: if `sender` is a registered router, decode the real user from `extensionData` and gate on that address instead.

3. **Document incompatibility and restrict to direct pool calls only.** The safest short-term fix: do not allowlist the router, require allowlisted users to call `pool.swap()` directly, and document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter`.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // enable router support

Attack:
  - Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...)                 // msg.sender in pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes successfully despite not being allowlisted

Verification (Foundry):
  1. Deploy SwapAllowlistExtension, configure pool with it
  2. setAllowedToSwap(pool, alice, true)
  3. setAllowedToSwap(pool, address(router), true)
  4. vm.prank(bob); router.exactInputSingle(...)           // bob is not allowlisted
  5. Assert swap succeeds — demonstrates full allowlist bypass
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
