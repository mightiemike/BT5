### Title
SwapAllowlistExtension checks router address instead of actual user, enabling allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the router is allowlisted on a curated pool (which is required for any allowlisted user to use the router), every non-allowlisted user can bypass the curation gate by routing through the router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ...)          // msg.sender = router
     → _beforeSwap(msg.sender=router, recipient, ...)
     → ExtensionCalling._beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the immediate caller) as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, i.e. `allowedSwapper[pool][router]`: [3](#0-2) 

Meanwhile, `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without forwarding the original `msg.sender` to the pool in any way the extension can observe: [4](#0-3) 

The same misbinding applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`. [5](#0-4) 

**The invariant broken:** the extension must gate the same actor the pool admin intended to allowlist. The pool admin allowlists individual user addresses; the extension checks the router address.

**The catch-22:** for any allowlisted user to use the router, the pool admin must allowlist the router. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check passes for every caller of the router regardless of their own allowlist status.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC-verified addresses, institutional partners, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps at the pool's oracle-anchored prices, draining LP-owned liquidity at rates the pool was not intended to offer them. This is a direct loss of LP principal and a complete curation failure.

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is the natural, expected configuration. The bypass is therefore reachable on every curated pool that supports router-mediated swaps, which is the common case.

---

### Recommendation

The extension must be able to identify the true economic actor, not the intermediary. Two sound approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into the `extensionData` it forwards to the pool. The extension decodes and verifies it, and the pool's `beforeSwap` hook validates the encoding (e.g., with a router-signed prefix). This requires a coordinated protocol-level convention.

2. **Check `recipient` instead of `sender` for swap allowlists**: For swap gating, the economically relevant actor is the recipient of the output tokens. The extension already receives `recipient` as its second parameter (currently ignored). Gating on `recipient` correctly identifies the beneficiary regardless of routing path, though it does not gate the payer.

3. **Require direct pool interaction for curated pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router; allowlisted users must call `pool.swap` directly. This is operationally fragile but requires no code change.

The cleanest fix is option 1, with the router encoding `abi.encode(msg.sender)` as a prefix in `extensionData` and the extension decoding it when `sender` is a known router address.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists alice (KYC'd user) and the router (so alice can use it)
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// alice adds liquidity directly (she is allowlisted)
// pool now has token0/token1 reserves

// Attack: bob (NOT allowlisted) calls the router
// bob is not in allowedSwapper[pool], but the router IS
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// ✓ swap succeeds — bob bypassed the allowlist
// Extension checked allowedSwapper[pool][router] == true, not allowedSwapper[pool][bob] == false
```

The extension's `beforeSwap` receives `sender = address(router)`, looks up `allowedSwapper[pool][router] == true`, and returns the success selector. Bob's swap executes at the oracle price against LP funds that were never intended to be available to him.

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
