Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is always `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router — the only way to let allowlisted users use the router — simultaneously grants every caller of the router the ability to bypass the per-user allowlist, completely defeating the extension's access-control purpose.

## Finding Description

**Call chain — confirmed in production code:**

`MetricOmmPool.swap` passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to every registered extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Root cause:** The extension checks the intermediary (`router`) rather than the economic actor (end user). Once `allowedSwapper[pool][router] = true`, the check passes for every caller of the router regardless of their individual allowlist status. There is no secondary check on the originating user, and `extensionData` is passed through from the router to the pool without any user-identity encoding. [6](#0-5) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute arbitrary swaps against the pool's oracle-derived bid/ask prices, draining LP value or front-running allowlisted participants. This is a complete, fund-impacting bypass of the pool's access-control boundary, meeting the "Admin-boundary break" and "direct loss of user principal/LP assets" impact criteria.

## Likelihood Explanation

The bypass requires the router to be allowlisted. A pool admin who wants allowlisted users to access the pool through the standard periphery path must allowlist the router — there is no other mechanism. This creates a forced choice: either allowlisted users cannot use the router at all, or the allowlist is rendered ineffective for all router callers. The second outcome is the expected production configuration, making the bypass reachable in any real deployment of this extension with router support.

## Recommendation

The extension must gate the economic actor (end user), not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. The extension can enforce router authenticity by checking that `sender` (the pool's `msg.sender`) is a factory-registered router before trusting the encoded user address.

2. **Trusted-router registry in the extension**: The extension maintains a set of trusted router addresses. When `sender` is a trusted router, it reads the originating user from a standardised field in `extensionData` and checks that address against `allowedSwapper[pool][originatingUser]` instead.

Either approach ensures the allowlist gates the user who controls the economic outcome of the swap.

## Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
3. Pool admin calls setAllowedToSwap(pool, router, true).
   (Required so alice can use MetricOmmSimpleRouter.)

Attack
──────
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})

5. Router calls pool.swap(bob, zeroForOne, amount, ...) 
   → msg.sender at pool = router address.

6. Pool calls _beforeSwap(router, bob, ...).

7. SwapAllowlistExtension.beforeSwap evaluates:
       allowAllSwappers[pool]       → false
       allowedSwapper[pool][router] → true  ← check passes

8. Swap executes; bob receives output tokens.
   The per-user allowlist is completely bypassed.

Foundry test sketch:
  vm.prank(bob);  // bob is NOT allowlisted
  router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
  // Expect: swap succeeds despite bob not being in allowedSwapper
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
