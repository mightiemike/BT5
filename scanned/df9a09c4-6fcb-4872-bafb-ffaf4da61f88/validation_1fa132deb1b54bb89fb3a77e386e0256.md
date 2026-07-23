### Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the originating user, enabling full allowlist bypass through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is documented to gate `swap` by **swapper address, per pool**. However, the `sender` argument it checks is `msg.sender` to the pool — which equals the **router contract address**, not the originating user, whenever a swap is routed through `MetricOmmSimpleRouter`. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the gate to every user on-chain.

---

### Finding Description

**Pool → extension argument binding**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

**Extension check**

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist: [3](#0-2) 

**Router call site**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The pool therefore sees `msg.sender = router`. No original-user identity is forwarded to the pool or to the extension: [4](#0-3) 

**The dilemma this creates for pool admins**

| Admin configuration | Direct swap by allowlisted user | Router swap by allowlisted user | Router swap by non-allowlisted user |
|---|---|---|---|
| Only individual users allowlisted (router NOT allowlisted) | ✅ passes | ❌ reverts (router not in list) | ❌ reverts |
| Router also allowlisted (to fix the above) | ✅ passes | ✅ passes | ✅ **bypasses allowlist** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institutional-only) with `SwapAllowlistExtension` and then allowlists the router — a natural step to let their approved users access the standard periphery — inadvertently grants every address on-chain the ability to swap in that pool. The allowlist guard fails completely open for all router-mediated swaps. This is a direct admin-boundary break: an unprivileged user bypasses a pool-admin-configured access control, enabling unrestricted swaps in a pool whose LP positions and fee revenue were predicated on a restricted participant set.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants their allowlisted users to access the router will allowlist it. The bypass requires no special privilege, no flash loan, and no contract deployment — a plain `exactInputSingle` call from any EOA suffices. The trigger is a routine, expected admin action (allowlisting the router), not a misconfiguration that requires insider knowledge to avoid.

---

### Recommendation

The extension must gate on the **economic actor** (the originating user), not the direct pool caller. Two viable approaches:

1. **Pass originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated change to the router and extension.
2. **Check `recipient` instead of `sender`**: For exact-input swaps the recipient is often the originating user; however this breaks for multi-hop paths where intermediate recipients are the router itself.
3. **Dedicated router allowlist slot**: Maintain a separate `allowedRouter` mapping; when `sender` is a known router, require that the decoded originating user (from `extensionData`) is individually allowlisted.

The simplest safe fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData` before calling the pool, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a recognized router.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router
   (without this, alice's router swaps revert)

Attack
──────
4. Bob (non-KYC'd, not individually allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient=bob, ...)
       → msg.sender to pool = router
6. Pool calls _beforeSwap(sender=router, ...)
7. SwapAllowlistExtension.beforeSwap:
       allowedSwapper[pool][router] == true  ✓
       → no revert
8. Bob's swap executes in the curated pool.
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
