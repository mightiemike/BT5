### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Any Caller to Bypass the Swap Allowlist on Curated Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When users interact through `MetricOmmSimpleRouter`, `msg.sender` of the pool's `swap` call is the **router contract**, not the end user. If the pool admin allowlists the router (the only way to let legitimate users use the standard periphery path), every unprivileged caller can bypass the allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → legitimate allowlisted users cannot use the standard periphery path at all.
- **Allowlist the router** → every user, including those the allowlist was meant to exclude, can bypass the gate by routing through the router.

The same structural problem applies to multi-hop `exactInput` and `exactOutput` paths.

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) loses that protection entirely once the router is allowlisted. Any unprivileged address can execute swaps against the pool's liquidity, draining LP value at oracle-anchored prices without the pool admin's consent. This is a direct loss of LP principal and a complete failure of the pool's access-control invariant.

### Likelihood Explanation

The router is the canonical, documented entry point for swaps in the Metric OMM periphery. Pool admins who want their allowlisted users to interact normally will allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA or contract can call `exactInputSingle` on the router pointing at the curated pool.

### Recommendation

The `sender` identity forwarded to extensions must reflect the **economic actor**, not the intermediary. Two complementary fixes:

1. **Router-side**: Store the originating `msg.sender` in transient storage at entry and expose it via a standard interface (e.g., `IMetricOmmSimpleRouter.msgSender()`). The extension can then read the real user from the router when `sender` is a known periphery contract.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode an optional `extensionData` field that the router populates with the real user address, and fall back to `sender` only when the caller is not a recognized periphery contract.

The cleanest long-term fix is for the pool to expose a `msgSender()` hook or for the router to pass the originating caller in `extensionData` so the allowlist can always gate the correct actor.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed user
  allowedSwapper[pool][router] = true  // admin must set this for alice to use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool: curated_pool,
      tokenIn: token0,
      amountIn: 1_000e18,
      recipient: bob,
      ...
    })

  Execution trace:
    router.exactInputSingle()          // msg.sender = bob
      pool.swap(recipient=bob, ...)    // msg.sender = router
        _beforeSwap(sender=router, ...)
          SwapAllowlistExtension.beforeSwap(sender=router)
            allowedSwapper[pool][router] == true  // PASSES
    bob receives token1 output         // allowlist bypassed
```

`alice` is the intended grantee; `bob` is the attacker. Because the extension sees `router` as `sender` and the router is allowlisted, `bob`'s swap succeeds. The pool's curated-access invariant is broken with no privileged action required beyond the admin's necessary step of allowlisting the router.

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
