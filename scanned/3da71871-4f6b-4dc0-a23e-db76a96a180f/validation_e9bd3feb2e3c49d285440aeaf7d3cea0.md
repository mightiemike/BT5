### Title
`SwapAllowlistExtension` gates on the router address instead of the originating user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the originating user. If the pool admin allowlists the router to enable router-based swaps for legitimate users, every unprivileged user can bypass the per-user allowlist by routing through the same public router.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
user (Bob, not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(...)   [msg.sender = Bob]
      → MetricOmmPool.swap(recipient, ...)         [msg.sender = router]
          → _beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  checks: allowedSwapper[pool][router]   ← router, not Bob
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the router is the immediate caller: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The router never forwards the originating user's address into the pool's `swap` call; the pool has no mechanism to receive it.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a named set of addresses. To allow those addresses to use the public router (the standard periphery entry point), the admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **any** address — including addresses the admin explicitly never allowlisted — can call `router.exactInputSingle` / `router.exactInput` / `router.exactOutput` and the extension check passes because it sees the router address, not the originating user. The allowlist is silently voided for all router-mediated paths.

Direct loss vector: unauthorized traders execute swaps against a pool whose oracle-anchored prices were configured for a restricted counterparty set, extracting value from LP positions that were deposited under the assumption of a curated trading environment.

---

### Likelihood Explanation

The router is the canonical, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router (rather than requiring direct pool calls) will add the router to the allowlist. This is the natural and expected operational pattern. The bypass therefore activates under normal, non-adversarial admin configuration.

---

### Recommendation

Pass the **originating user** through the swap path so the extension can gate on the economically relevant actor. Two concrete options:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the originating `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when present.
2. **Pool-side**: Add an optional `originator` field to the `swap` signature (or a separate transient-storage slot set by the router before calling the pool) so the pool can forward the true initiator to extensions.

Either way, the allowlist check must key on the address that controls the economic decision to swap, not the intermediate contract that relays the call.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it

Attack:
  - Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Bob's swap executes successfully despite never being allowlisted
```

The invariant "only allowlisted addresses may swap on this pool" is broken for every pool that allowlists the router. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
