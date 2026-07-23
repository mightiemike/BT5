Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router's address, not the end user's. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently opens the pool to all users, allowing non-allowlisted addresses to extract LP principal at oracle-derived prices.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly — the router is `msg.sender` of that call, so `sender` arriving at the extension is the router address, not the end user: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The `DepositAllowlistExtension` does not share this flaw — it correctly checks `owner` (the position owner), which is passed explicitly and is independent of the call path: [6](#0-5) 

When the pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for permitted users, `allowedSwapper[pool][router]` becomes `true`. Any caller of the router — including non-allowlisted addresses — then passes the check because the extension sees the router address as `sender`, not the individual user.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the natural operational step to enable router-based swaps for permitted users) inadvertently opens the pool to all users. Non-allowlisted users can execute swaps against LP-owned liquidity at oracle-derived prices, extracting value from bins the admin intended to restrict. This is a direct loss of LP principal and a broken core pool invariant: the allowlist guard fails open on the supported periphery path.

## Likelihood Explanation
The trigger requires the pool admin to have allowlisted the router address, which is the expected operational step when a curated pool is meant to be accessible via the standard periphery. No privileged access beyond the pool admin's own intended configuration is required. Any unprivileged user can exploit it by calling the public router functions (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

## Recommendation
The extension must identify the economic actor, not the proximate caller. Two sound approaches:

1. **Pass originator through `extensionData`**: Have the router encode `msg.sender` (the end user) into `extensionData` for each hop, and have `SwapAllowlistExtension` decode and check that address instead of `sender`. The extension must also verify that `msg.sender` (the pool) is a registered pool so the data cannot be spoofed by a direct pool call that injects a fake originator.

2. **Check `sender` only for direct calls; require originator field for router calls**: Add a convention where the router always includes a factory-verified originator field in `extensionData`, and the extension falls back to `sender` only when no such field is present and `sender` is not a known router.

Either way, the allowlist lookup key must be the address that economically controls the swap, not the intermediate contract that relays it.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured as beforeSwap hook
  admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    // Admin intends: "allow router-mediated swaps for my allowlisted users"
    // Effect: allowedSwapper[pool][router] = true

Attack:
  attacker = address NOT in allowedSwapper[pool]
  attacker calls: router.exactInputSingle({pool: pool, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
        msg.sender of pool.swap() = router
      → _beforeSwap(router, recipient, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            allowedSwapper[pool][router] == true  ← passes
      → swap executes, LP funds transferred to attacker's recipient

Result:
  Non-allowlisted attacker swaps on a curated pool.
  LP principal is transferred at oracle price to the attacker's chosen recipient.
  The allowlist guard is silently bypassed.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, call `setAllowedToSwap(pool, router, true)`, then call `router.exactInputSingle` from an address not individually allowlisted and assert the swap succeeds. Also assert that calling `pool.swap` directly from the same non-allowlisted address reverts with `NotAllowedToSwap`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
