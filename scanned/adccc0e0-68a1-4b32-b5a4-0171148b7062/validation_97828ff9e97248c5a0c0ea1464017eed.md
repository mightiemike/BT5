### Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` delivered to the extension is the router address — not the end user. If the pool admin allowlists the router (a natural step to enable router-based swaps), every user on the network can bypass the individual-address allowlist by calling any of the router's public entry points.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the router contract address, so `sender` delivered to the extension is `address(router)`, not the end user. The same is true for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The pool admin who wants to enable router-based swaps for their curated pool will call `setAllowedToSwap(pool, router, true)`. Once the router is on the allowlist, `allowedSwapper[pool][router]` is `true`, and the check passes for **every** caller who routes through the router — regardless of whether that caller is individually allowlisted.

---

### Impact Explanation

Any unprivileged user can bypass the swap allowlist on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting that pool. The pool admin's intent — restricting swaps to a specific set of addresses (e.g., KYC-verified counterparties) — is silently voided. Non-allowlisted users can drain liquidity from the pool at oracle-quoted prices, causing direct loss of LP principal and breaking the curation invariant the pool was deployed to enforce.

---

### Likelihood Explanation

The pool admin must allowlist the router for the bypass to be active. This is a natural and expected operational step: without it, even individually allowlisted users cannot use the supported periphery to swap. The admin has no on-chain signal that allowlisting the router opens the pool to all users; the extension's name and interface imply per-address gating. The trigger is therefore a routine, non-malicious admin action that the code design actively encourages.

---

### Recommendation

`SwapAllowlistExtension` must not check `sender` (the direct pool caller) when the pool is expected to be reached through the router. Two options:

1. **Document and enforce direct-call-only**: Add a check that `sender == tx.origin` or that `sender` is not a known router, and document that the extension is incompatible with `MetricOmmSimpleRouter`.
2. **Pass the end user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it, combined with a signature or trusted-forwarder pattern so the value cannot be spoofed.

Option 2 is the safer long-term fix because it preserves router usability while enforcing per-user policy.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension configured as beforeSwap hook
  admin calls setAllowedToSwap(pool, alice, true)          // alice is KYC'd
  admin calls setAllowedToSwap(pool, router, true)         // enable router-based swaps

Attack (executed by bob, who is NOT allowlisted):
  bob calls router.exactInputSingle({
      pool:            <curated pool>,
      tokenIn:         token0,
      tokenOut:        token1,
      zeroForOne:      true,
      amountIn:        X,
      amountOutMinimum: 0,
      recipient:       bob,
      ...
  })

Pool.swap is called with msg.sender = router.
_beforeSwap passes sender = router to SwapAllowlistExtension.
Extension checks: allowedSwapper[pool][router] == true  → passes.
Bob's swap executes at oracle price, bypassing the individual allowlist.
``` [6](#0-5) [7](#0-6)

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
