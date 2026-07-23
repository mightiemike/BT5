Audit Report

## Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Router Address Substitutes for True Swapper Identity - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates a swap, `sender` becomes the router address rather than the originating user. If the router is allowlisted for a pool — the necessary configuration for router-mediated swaps to work — any unprivileged user can bypass the per-user restriction by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is whatever address called `pool.swap()`. [1](#0-0) 

In `MetricOmmPool.swap`, the value forwarded to `_beforeSwap` as `sender` is `msg.sender` of the swap call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    ...
``` [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly, making itself `msg.sender` to the pool. The originating user's address (`msg.sender` to the router) is stored only in transient callback context for payment purposes — it is never forwarded to the pool or extension as the swapper identity:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← caller-controlled, extension does not decode user identity from it
    );
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` with the router as `msg.sender`. [4](#0-3) 

The extension has no mechanism to decode a real user identity from `extensionData`; it only checks the `sender` parameter. Since `sender` is the router when the router intermediates, and the router must be allowlisted for legitimate router-mediated swaps to function, the per-user restriction is completely nullified for all users who call the router.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional participants, or whitelisted market makers) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool; the extension sees `sender = router`, finds it allowlisted, and approves the swap. LPs in the restricted pool suffer impermanent loss and fee dilution from unauthorized swaps they never consented to. This constitutes broken core pool functionality causing loss of LP principal — matching the "broken core pool functionality causing loss of funds" impact criterion.

## Likelihood Explanation
The router is a public, permissionless contract callable by any address. The pool admin must allowlist the router to make the extension usable for router-mediated swaps by legitimate users — this is the expected operational configuration. Once the router is allowlisted, the bypass requires zero privilege and zero special setup: a single `exactInputSingle` call suffices. The bypass is permanent until the admin removes the router from the allowlist, which simultaneously breaks all legitimate router usage for that pool.

## Recommendation
The extension must verify the originating user, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the true initiator through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. The extension decodes and checks that address against the allowlist. This is acceptable if the router is a known, immutable contract.

2. **Treat the router as transparent**: When `sender` is a known router address, require that `extensionData` contains a valid user identity, decode it, and check that identity against the allowlist. Reject calls where `sender` is the router but no valid user identity is provided.

The extension must never grant access based solely on the router's address being allowlisted.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only intended swapper
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it via router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...}) // bob is msg.sender to router

  router calls:
    pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    // msg.sender to pool = router address

  pool calls _beforeSwap(msg.sender=router, ...) which calls:
    extension.beforeSwap(sender=router, ...)
    // allowedSwapper[pool][router] == true → passes

  Result: bob swaps successfully against the restricted pool.
          The allowlist check on alice vs. bob is never performed.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, call `router.exactInputSingle` from `bob` (not allowlisted), assert the swap succeeds and `bob` receives output tokens.

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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
