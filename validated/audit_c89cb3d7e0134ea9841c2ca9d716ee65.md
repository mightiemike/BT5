### Title
`SwapAllowlistExtension` Gates Router Identity Instead of User Identity, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the originating user. An admin who allowlists the router to support router-based swaps for legitimate users inadvertently opens the gate to every user on the network.

---

### Finding Description

The pool's `swap` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The router stores the original user only in its own transient callback context; it is never forwarded to the pool or the extension. The extension has no mechanism to recover the originating user — the `extensionData` field is ignored by `SwapAllowlistExtension`, and even if it were read, its contents are fully user-controlled and cannot be trusted.

**The identity mismatch is structural**: the allowlist is keyed on the direct caller of `pool.swap`, but the economically relevant actor is the user who called the router. These two addresses are always different when the router is used.

This creates an irresolvable dilemma for any admin who wants to:
1. Restrict swaps to a specific set of users (e.g., KYC'd participants), **and**
2. Allow those users to swap through the public router.

To satisfy (2), the admin must add the router to `allowedSwapper`. Once the router is allowlisted, condition (1) is void — any address can call `router.exactInputSingle` and the extension will pass because `sender = router` is allowlisted.

---

### Impact Explanation

Any user can bypass a per-user swap allowlist on any pool that has configured `SwapAllowlistExtension` and also allowlisted the router. The pool's intended access control (e.g., KYC gate, institutional-only restriction) is completely defeated. Unauthorized users can execute swaps against pool liquidity, extracting value from LP positions that were deposited under the assumption that only vetted counterparties would trade.

This is a direct loss-of-principal risk for LPs: they provided liquidity expecting a restricted counterparty set; the bypass exposes them to unrestricted adverse selection.

---

### Likelihood Explanation

The scenario is reachable through a natural, good-faith admin configuration. A pool admin who wants to support both direct and router-based swaps for their allowlisted users will allowlist the router. The bypass requires no privileged access, no malicious setup, and no non-standard tokens — only a call to the public `MetricOmmSimpleRouter.exactInputSingle` function. Any user who discovers the allowlisted router can exploit it immediately.

---

### Recommendation

The extension must check the **originating user**, not the direct pool caller. Two sound approaches:

1. **Pass the original user through `extensionData` with router-side signing or a trusted forwarder pattern**: the router encodes `msg.sender` into `extensionData` and the extension verifies it against a trusted router registry. This requires the extension to know which routers are trusted.

2. **Check `sender` only when `sender` is not a known router; otherwise revert**: pools that require per-user allowlisting should prohibit router-mediated swaps entirely by reverting when `sender` is a router address, forcing users to call the pool directly.

The simplest safe fix is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and add a guard in `beforeSwap` that reverts if `sender` is a registered router:

```solidity
// In beforeSwap:
if (isRouter[sender]) revert RouterNotAllowed();
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)       // alice is KYC'd
  - Admin calls setAllowedToSwap(pool, router, true)      // enable router for alice
  - bob is NOT allowlisted

Attack:
  - bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=bob, ...)  →  msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for bob despite bob not being allowlisted

Result:
  - bob swaps against LP liquidity in a pool intended to be restricted to alice only
  - The allowlist invariant is broken; LPs are exposed to unrestricted counterparties
``` [3](#0-2) [5](#0-4) [1](#0-0)

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
