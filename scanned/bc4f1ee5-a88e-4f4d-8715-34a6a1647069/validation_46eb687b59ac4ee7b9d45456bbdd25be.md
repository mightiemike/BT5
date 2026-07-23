### Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the actual user, allowing any address to bypass a curated pool's swap allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the user. A pool admin who adds the router to the allowlist (the only way to let allowlisted users trade via the router) inadvertently opens the pool to every address, completely defeating the curation invariant.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured `beforeSwap` extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router becomes the direct caller of `pool.swap`: [4](#0-3) 

So the extension receives `sender = address(router)`. The allowlist check evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for any pool admin who wants to run a curated pool accessible via the standard router:

| Router in allowlist? | Effect |
|---|---|
| No | Allowlisted users cannot use the router at all (broken flow) |
| Yes | **Every** address can swap through the router (allowlist bypassed) |

There is no configuration that achieves "only allowlisted users may swap via the router."

---

### Impact Explanation

**Medium.** A curated pool's swap allowlist is completely bypassed. Any unprivileged address can execute swaps in a pool the admin intended to restrict to specific counterparties. This breaks the core curation invariant and allows unauthorized parties to trade against pool liquidity, potentially extracting value from LP positions that were priced for a controlled set of counterparties.

---

### Likelihood Explanation

**Medium.** The bypass requires the router to be on the allowlist. Adding the router is the natural and expected action for any pool admin who wants their allowlisted users to access the pool through the standard periphery. The admin's intent is to enable their users, not to open the pool to everyone; the design flaw makes those two goals mutually exclusive without any documentation warning.

---

### Recommendation

Gate on the economic actor, not the immediate pool caller. Two options:

1. **Pass the originating user through the router.** Have `MetricOmmSimpleRouter` encode the real `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check it. This requires a trust assumption that only the known router populates that field.

2. **Check `recipient` as a proxy for the user.** For single-hop exact-input swaps the recipient is often the user, but this breaks for multi-hop paths and exact-output flows.

The cleanest fix is option 1 combined with an `onlyPool` guard that also validates the pool's registered router, so the extension can trust the encoded actor.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  pool admin calls setAllowedToSwap(pool, userA, true)       // allowlist userA
  pool admin calls setAllowedToSwap(pool, router, true)      // needed so userA can use router

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: userB})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=userB, ...)          [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes)
      → swap executes, userB receives output tokens

Result: userB, who is not on the allowlist, successfully swaps in the curated pool.
``` [3](#0-2) [1](#0-0) [4](#0-3)

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
