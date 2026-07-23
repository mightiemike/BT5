The call chain is fully traceable. Here is the definitive assessment.

---

## Call Chain Trace

**Direct swap:**
```
User → MetricOmmPool.swap()
  msg.sender = user
  _beforeSwap(sender=user, ...)          [ExtensionCalling.sol:160-176]
  → extension.beforeSwap(sender=user)
  → allowedSwapper[pool][user]           ✓ correct actor
```

**Router-mediated swap:**
```
User → MetricOmmSimpleRouter.exactInputSingle()
  → pool.swap(recipient, ...)            [MetricOmmSimpleRouter.sol:72-80]
    msg.sender = router
    _beforeSwap(sender=router, ...)      [ExtensionCalling.sol:160-176]
    → extension.beforeSwap(sender=router)
    → allowedSwapper[pool][router]       ✗ wrong actor — checks router, not user
```

The `sender` argument forwarded to the extension is always `msg.sender` from the pool's perspective. [1](#0-0) [2](#0-1) 

When the router calls `pool.swap()`, `msg.sender` inside the pool is the router. That address is passed as `sender` to `_beforeSwap`, which encodes it and forwards it to the extension. The extension then checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. [3](#0-2) [4](#0-3) 

---

### Title
Router-Mediated Swaps Check Wrong Actor in `SwapAllowlistExtension.beforeSwap`, Allowing Any User to Bypass Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes the direct caller, so the hook checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the router is allowlisted (a natural configuration for any pool that wants to support router-based swaps), the per-user allowlist is completely bypassed.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value and forwards it to the extension: [5](#0-4) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [6](#0-5) 

When the router calls `pool.swap()`, `sender` = router address. The check becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (to permit router-based swaps for legitimate users), **every user** can call the router and pass the check, regardless of whether they are individually allowlisted.

`MetricOmmSimpleRouter.exactInputSingle` (and all `exact*` variants) calls `pool.swap()` directly as `msg.sender`: [7](#0-6) 

There is no mechanism in the router to forward the original `msg.sender` (the end user) to the pool or extension.

### Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC'd users, whitelisted counterparties). To allow those users to also use the router, the admin must allowlist the router address. Once the router is allowlisted, the per-user restriction is nullified: any unprivileged address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and execute swaps on the restricted pool. The allowlist policy is completely bypassed.

This is a direct policy bypass on curated pools — the exact impact the `SwapAllowlistExtension` is designed to prevent.

### Likelihood Explanation
Any pool that uses `SwapAllowlistExtension` and also wants to support the official router must allowlist the router. This is the natural and expected configuration. The bypass is therefore triggered by normal, intended usage of the protocol's own periphery contracts.

### Recommendation
The extension must check the **economic actor** (the end user), not the intermediary. Two approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension reads and verifies it. This requires a convention between router and extension.
2. **Check `recipient` instead of `sender`**: For swap allowlists, the recipient is often the meaningful actor. However, this is also not always the end user.
3. **Preferred — dedicated router-aware allowlist**: The extension should accept a trusted router list and, when `sender` is a trusted router, extract the real user from `extensionData` (signed or encoded by the router at call time).

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true      // alice is the only allowed user
  allowedSwapper[pool][router] = true     // admin allowlists router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})
  
  router calls pool.swap() → msg.sender = router
  _beforeSwap(sender=router, ...)
  extension checks allowedSwapper[pool][router] → true
  bob's swap succeeds despite not being allowlisted
```

Direct assertion: `allowedSwapper[pool][bob]` is `false`, yet bob's swap through the router succeeds because `allowedSwapper[pool][router]` is `true`. The hook checked the wrong actor.

---

**Verdict: Real vulnerability.** The `SwapAllowlistExtension` cannot enforce per-user swap restrictions when the official router is used, because it checks the router's address rather than the end user's address. Any pool that allowlists the router to support router-based swaps for legitimate users simultaneously opens itself to all router users.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
