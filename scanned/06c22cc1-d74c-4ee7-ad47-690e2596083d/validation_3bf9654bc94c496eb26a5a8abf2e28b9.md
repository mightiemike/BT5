### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual Swapper, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the user. A pool admin who allowlists the router to let legitimate users trade through it simultaneously opens the pool to every non-allowlisted address.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses that argument as the identity to check against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap` is the router contract, so `sender` delivered to the extension is the router address, not the originating user. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

---

### Impact Explanation

Two mutually exclusive failure modes arise for any pool that configures `SwapAllowlistExtension`:

**Mode A – Allowlisted users cannot use the router.**  
The admin allowlists individual users (e.g., Alice). Alice calls `exactInputSingle`; the extension sees `sender = router`, which is not allowlisted, and reverts with `NotAllowedToSwap`. The router — the protocol's primary swap interface — is unusable for every curated pool.

**Mode B – Admin allowlists the router to restore usability; allowlist is fully bypassed.**  
To fix Mode A the admin adds the router to `allowedSwapper[pool][router] = true`. Now `allowedSwapper[pool][router]` is true for every call that arrives through the router, regardless of who the originating user is. Any non-allowlisted address (Bob) calls `exactInputSingle` → router calls `pool.swap()` → extension sees `sender = router` → check passes → Bob swaps freely on a pool that was supposed to be curated. The allowlist provides zero protection.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, and to any future periphery contract that calls `pool.swap()` directly.

---

### Likelihood Explanation

The router is the canonical user-facing entry point documented and deployed alongside the pool system. Pool admins operating curated pools will encounter Mode A the first time an allowlisted user tries to use the router, and the natural remediation is to allowlist the router, triggering Mode B. No privileged attacker capability is required beyond calling a public router function. The trigger is a routine admin action taken in response to an observed UX failure.

---

### Recommendation

The extension must check the economic actor, not the intermediary. Two sound approaches:

1. **Pass the originating user explicitly.** Add a `payer` or `originator` field to the swap call path (e.g., as part of `extensionData`) that the router populates with `msg.sender` before calling the pool. The extension reads and checks that field.

2. **Check `recipient` instead of `sender` for identity gating.** If the pool's `recipient` is always the actual user, the extension can gate on the second argument. This requires verifying that the router always sets `recipient = msg.sender` for single-hop swaps, which is true for `exactInputSingle` but not for multi-hop hops where `recipient` is `address(this)`.

3. **Require direct pool calls for curated pools.** Document that pools with `SwapAllowlistExtension` must not be used with the shared router; deploy a pool-specific router that enforces identity before calling the pool.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls extension.setAllowedToSwap(pool, alice, true)
  admin calls extension.setAllowedToSwap(pool, router, true)   // "fix" for Mode A

Attack (Bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  → router calls pool.swap(bob, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; bob receives tokens from a curated pool
  → allowlist invariant broken
```

The corrupted value is `allowedSwapper[pool][router]`: a single entry that was intended to represent the router as a trusted intermediary but instead acts as a universal pass for every caller of that router on that pool.

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
