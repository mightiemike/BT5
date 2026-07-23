### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is the pool's `msg.sender` — the direct caller of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end user. If the pool admin adds the router to the allowlist (the only way to let allowlisted users swap through the router), any unprivileged user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

The `SwapAllowlistExtension` is designed to gate swap access on curated pools. Its `beforeSwap` hook receives `sender` from the pool, which equals the pool's `msg.sender` — the direct caller of `pool.swap()`.

**Call chain when routing through `MetricOmmSimpleRouter`:**

1. User calls `router.exactInputSingle(...)` (or `exactInput`, `exactOutput`, `exactOutputSingle`)
2. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — here `msg.sender` of the pool is the **router**
3. Pool calls `_beforeSwap(msg.sender = router, recipient, ...)` in `ExtensionCalling`
4. `SwapAllowlistExtension.beforeSwap(sender = router, ...)` checks `allowedSwapper[pool][router]`

The allowlist check is against the **router address**, not the actual user. This creates two broken scenarios:

**Scenario A — Router not allowlisted:** Allowlisted users cannot swap through the router. They must call `pool.swap()` directly, losing slippage protection and multi-hop routing. Core swap functionality is broken for the intended audience.

**Scenario B — Router allowlisted:** Pool admins who add the router to the allowlist (the only way to enable router-based swaps for their users) inadvertently allow **all** users to bypass the allowlist, since any address can route through the router.

The root cause is that `sender` in `beforeSwap` is the pool's `msg.sender` (the router), not the originating user. The router stores the actual user's address only in transient callback context for payment purposes — it is never surfaced to the extension hook. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

**High.** The `SwapAllowlistExtension` is the sole access-control mechanism for curated swap pools. The bug completely breaks the allowlist invariant when the router is involved. In Scenario B, any unprivileged user can bypass the allowlist on a curated pool by routing through `MetricOmmSimpleRouter`, executing trades on a pool they should not have access to. This is a direct policy bypass with fund-impacting consequences: unauthorized users trade against restricted LP positions, potentially draining value from pools designed for controlled counterparties.

---

### Likelihood Explanation

**Medium-High.** `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Curated pools with `SwapAllowlistExtension` are a documented use case. Any pool admin who wants their allowlisted users to use the router must add the router to the allowlist, directly triggering Scenario B. Even without Scenario B, Scenario A breaks core swap functionality for allowlisted users on the supported periphery path.

---

### Recommendation

The extension must identify the actual end user, not the intermediary router. Concrete options:

1. **Router encodes caller in `extensionData`:** The router appends `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool. The extension decodes and checks this value. This requires trusting the router, which is acceptable since it is a protocol-controlled contract.
2. **Gate `recipient` instead of `sender`:** For single-hop swaps, `recipient` is typically the actual user. This is imperfect for multi-hop paths where intermediate recipients are the router itself.
3. **Document incompatibility:** Explicitly document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and require direct `pool.swap()` calls on allowlisted pools.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: allowedSwapper[pool][userA] = true   (userA is the intended gated user)
  - Pool admin: allowedSwapper[pool][router] = true  (added so userA can use the router)

Attack (userB, not allowlisted):
  1. userB calls router.exactInputSingle({pool: pool, recipient: userB, ...})
  2. Router calls pool.swap(...) — pool sees msg.sender = router
  3. Pool calls _beforeSwap(sender = router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes successfully for userB

Result:
  userB, who is not in the allowlist, successfully trades on the curated pool
  by routing through MetricOmmSimpleRouter. The allowlist is completely bypassed.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
