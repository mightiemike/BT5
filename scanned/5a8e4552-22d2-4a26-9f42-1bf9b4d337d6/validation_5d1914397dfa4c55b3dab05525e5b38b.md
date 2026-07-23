### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the pool's `msg.sender` — the router contract — not the end user. When a pool admin allowlists the router to enable router-mediated swaps for their curated pool, every unpermissioned user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the pool's caller: [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap`: [4](#0-3) 

So the pool's `msg.sender` — and therefore the `sender` the extension sees — is the **router address**, not the end user. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to swap through the router must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller, regardless of who the actual user is. Any non-allowlisted address can then bypass the curation policy by routing through `MetricOmmSimpleRouter`.

The same wrong-actor binding applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`, all of which call `pool.swap` with the router as `msg.sender`. [5](#0-4) 

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties loses that restriction entirely once the router is allowlisted. Any unpermissioned address can execute swaps against the pool, causing unauthorized price impact, violating compliance or institutional access policies, and potentially draining LP value through adversarial trading that the allowlist was designed to prevent. This is a direct broken-core-functionality / admin-boundary-break impact.

### Likelihood Explanation

The router is the standard user-facing entry point documented and shipped with the protocol. A pool admin who wants their allowlisted users to be able to use the router (the normal UX path) will naturally allowlist the router address. The bypass is then reachable by any public user with no special privileges, no malicious setup, and no non-standard tokens.

### Recommendation

The extension must resolve the actual end-user identity rather than the direct pool caller. Two approaches:

1. **Pass the real initiator through the call chain.** Have the router store the originating `msg.sender` in transient storage (analogous to how it already stores the payer in `_setNextCallbackContext`) and expose a getter. The extension reads that value when `sender` is a known router.

2. **Check `sender` only when it is not a known router; otherwise read the router's stored initiator.** This requires the extension to be aware of trusted router addresses, which the pool admin can configure.

Either way, the invariant must be: the identity checked against the allowlist is the address that economically initiates and benefits from the swap, not the intermediate contract that relays it.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to let allowlisted users reach the pool via the router.
3. Non-allowlisted EOA (attacker) calls:
     MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(...) — pool's msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
7. Swap executes. Attacker is never checked against the allowlist.
```

The attacker pays no fee beyond gas, requires no special role, and the bypass is repeatable every block.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
