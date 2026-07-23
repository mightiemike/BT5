Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is `msg.sender` of the `pool.swap()` call — the direct caller, not the economic actor. When swaps are routed through `MetricOmmSimpleRouter`, that direct caller is the router contract. If the pool admin allowlists the router (the natural configuration to let allowlisted users access the pool via the official periphery), every non-allowlisted user can bypass the per-user swap gate by routing through the router, completely nullifying the access-control boundary the pool admin intended to enforce.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` — the direct caller of `pool.swap()` — as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end user
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool):

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern holds for `exactInput` (all hops): [5](#0-4) 

And for `exactOutputSingle` and `exactOutput` (recursive hops inside `_exactOutputIterateCallback`): [6](#0-5) [7](#0-6) 

In every case, `sender` received by the extension is the router address, not the actual end user. There is no existing guard that recovers the original initiator from `extensionData` or any other field.

**Two broken invariants:**

| Pool admin intent | What actually happens |
|---|---|
| Allowlist specific users; block everyone else | Allowlisted users are also blocked when they use the router (router not allowlisted) |
| Allowlist the router so allowlisted users can use it | Every non-allowlisted user can now swap through the router — full bypass |

The pool admin cannot simultaneously (a) let allowlisted users use the router and (b) block non-allowlisted users from using the router, because the extension cannot distinguish the two.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or private LP partners) is fully bypassable by any unprivileged user who calls `MetricOmmSimpleRouter`. The attacker executes swaps at oracle-derived bid/ask prices in a pool that was explicitly configured to deny them access. This constitutes an admin-boundary break: an unprivileged path bypasses the access-control restriction the pool admin paid to enforce, nullifying the core invariant of the `SwapAllowlistExtension`. The pool is exposed to any counterparty the admin intended to exclude.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who wants allowlisted users to have a normal UX must allowlist the router, which simultaneously opens the pool to all users. The bypass is reachable through a routine, expected configuration step, not an exotic mistake. No privileged key beyond the pool admin's own allowlist setter is required, and the attacker needs only to call the public router.

## Recommendation
The extension must gate on the economically relevant actor — the end user — not the direct caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original initiator through the router.** Have `MetricOmmSimpleRouter` forward `msg.sender` as an authenticated field inside `extensionData` (signed or verified via a trusted forwarder pattern), and have `SwapAllowlistExtension` decode and check that field when the caller is a known router.

2. **Check `recipient` as a proxy for the end user** (simpler but less precise): the router always sets `recipient` to the user-supplied address; the extension could check `recipient` instead of `sender` when `sender` is a known router address.

3. **Alternatively**, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level by preventing pools that use `SwapAllowlistExtension` from being accessed through the router.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(bob, ...)          // msg.sender = router
        → _beforeSwap(router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ PASS
      → swap executes at oracle price
      → bob receives output tokens

Result: bob, a non-allowlisted address, successfully swaps in a pool
        that was configured to deny him access.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
