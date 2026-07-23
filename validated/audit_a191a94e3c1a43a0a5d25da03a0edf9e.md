Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any Swapper to Bypass Curated Pool Restrictions - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to support the standard periphery path simultaneously opens the pool to every user on-chain, completely defeating the allowlist invariant.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool — the original `msg.sender` (the end user) is only stored in transient callback context for payment, never forwarded to the pool as `sender`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the router is the direct caller of `pool.swap()`: [5](#0-4) 

This creates an irresolvable dilemma for any pool admin who configures a swap allowlist:

- **If the router is NOT allowlisted:** every allowlisted user who calls through the router is blocked. The standard periphery path is broken for all legitimate users.
- **If the router IS allowlisted:** the check becomes `allowedSwapper[pool][router] == true`, which passes for every caller regardless of their individual allowlist status. Any non-allowlisted user can bypass the guard by routing through `MetricOmmSimpleRouter`.

No existing guard in the extension, pool, or router prevents this. The extension has no awareness of router intermediaries, and the router has no mechanism to forward the originating user's address to the pool's `sender` parameter.

## Impact Explanation

A curated pool's swap allowlist is completely defeated. Any unprivileged user can execute swaps in a pool designed to restrict trading to a specific set of addresses. This constitutes broken core pool functionality with direct LP fund-loss potential: non-allowlisted actors can perform unrestricted price manipulation, extract LP value through arbitrage or sandwich attacks that the allowlist was designed to prevent, and violate the pool's curation invariant. This meets the "Broken core pool functionality causing loss of funds" impact criterion.

## Likelihood Explanation

Likelihood is high. `MetricOmmSimpleRouter` is the canonical periphery swap path. Any pool admin who configures a `SwapAllowlistExtension` and then allowlists the router — a natural operational step to support standard tooling for legitimate users — triggers the bypass. The attacker needs no special privileges: a single call to `exactInputSingle` with any `extensionData` the pool accepts is sufficient. The condition is self-inflicted by correct operational use of the system.

## Recommendation

The extension must gate on the economic actor, not the direct pool caller. Two sound approaches:

1. **Pass the original initiator through the router.** Add an `originator` field to the swap call that the router populates with `msg.sender` before calling the pool, and have the pool forward that value as a separate argument to extensions.
2. **Check `sender` in the extension against a router-aware registry.** If `sender` is a known router, read the router's stored payer/originator from transient storage (already used for callback context) and check that address against the allowlist instead.

The simplest safe fix is option 1: the pool should pass both the direct caller (`msg.sender`) and a user-supplied `originator` parameter, and the extension should check the originator.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  admin allowlists alice: allowedSwapper[pool][alice] = true
  admin allowlists router: allowedSwapper[pool][router] = true
    (necessary so alice can use the standard periphery)

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
    pool: pool,
    recipient: bob,
    ...
  })

  Router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
    → pool.msg.sender = router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob despite bob not being allowlisted

Result:
  bob swaps successfully in a pool that was supposed to block him.
  The allowlist is completely bypassed for any user who routes through
  MetricOmmSimpleRouter.
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
