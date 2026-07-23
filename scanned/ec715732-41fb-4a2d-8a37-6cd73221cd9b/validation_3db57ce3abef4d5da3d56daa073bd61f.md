### Title
SwapAllowlistExtension Checks the Router's Address Instead of the Original User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks whether the **router** is allowlisted — not whether the **original user** is allowlisted. Any pool admin who allowlists the router to support standard router-mediated swaps simultaneously opens the allowlist to every user on the internet.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the hook.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`_beforeSwap` forwards that value verbatim as the `sender` argument to every configured extension: [2](#0-1) 

**Step 2 — The router calls `pool.swap` directly, so `msg.sender` inside the pool is the router.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` with no forwarding of the original user's identity: [3](#0-2) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Step 3 — The allowlist extension checks `sender`, which is the router, not the user.** [4](#0-3) 

`msg.sender` inside the extension is the pool (correct). `sender` is the router address. The check `allowedSwapper[pool][router]` is evaluated, not `allowedSwapper[pool][user]`.

**Step 4 — The router must be allowlisted for any router-mediated swap to succeed.**

A pool admin who wants allowlisted users to trade through the standard router must add the router to `allowedSwapper`. Once that entry exists, the condition `allowedSwapper[pool][router]` is `true` for every caller of the router, regardless of who they are. The per-user allowlist is completely bypassed.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses all access-control guarantees the moment the router is allowlisted. Any address — including addresses the pool admin explicitly excluded — can execute swaps against the pool by routing through `MetricOmmSimpleRouter`. This is a direct policy bypass with fund-impacting consequences: unauthorized users can drain liquidity at oracle-quoted prices from a pool that was designed to be private.

---

### Likelihood Explanation

The router is the canonical, documented entry point for swaps in the Metric OMM periphery. A pool admin who deploys `SwapAllowlistExtension` and also wants their allowlisted users to use the standard router UI has no choice but to allowlist the router — there is no mechanism to forward the original user's identity. The misconfiguration is therefore a natural operational outcome, not an exotic edge case.

---

### Recommendation

The extension must be able to verify the **economic actor** (the original user), not the **proximate caller** (the router). Two approaches:

1. **Encode the original user in `extensionData`**: The router includes `msg.sender` in the `extensionData` it forwards to the pool. The extension decodes and verifies that address. The pool admin allowlists users, not the router. (Requires a convention between router and extension.)

2. **Check `sender` AND `recipient` or require direct-pool-only swaps**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` is a known router address, forcing allowlisted users to call the pool directly.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so users can swap
  admin does NOT call setAllowedToSwap(pool, userB, true)  // userB is NOT allowlisted

Attack:
  userB calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓
    → swap executes — userB trades on a pool they were explicitly excluded from
```

The allowlist check passes because it evaluates the router's allowlist entry, not userB's. The invariant "only allowlisted addresses may swap on this pool" is broken for every user who routes through `MetricOmmSimpleRouter`.

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
