### Title
`SwapAllowlistExtension` checks the router's address as `sender` instead of the actual end-user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap` sets `sender = msg.sender` (the direct caller of the pool), any swap routed through `MetricOmmSimpleRouter` presents the router's address as the swapper identity. A pool admin who allowlists the router address to enable router-mediated swaps for their curated pool inadvertently opens the pool to every user, completely bypassing the per-user allowlist.

---

### Finding Description

**Actor binding chain:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

**Router path:**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactOutputSingle`, `exactInput`, `exactOutput`) calls `pool.swap(...)` directly. The router is therefore `msg.sender` of the pool's `swap` call: [4](#0-3) 

The actual end-user (`msg.sender` of the router call) is never forwarded to the pool or to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Bypass scenario:**

A pool admin configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties). To allow those users to trade through the standard periphery router, the admin allowlists the router address. Because the extension checks the router's address — not the individual user's address — every user who routes through `MetricOmmSimpleRouter` now passes the allowlist check, regardless of whether they are in the curated set.

**Contrast with `DepositAllowlistExtension`:**

The deposit-side extension correctly gates by `owner` (the position owner explicitly passed as a parameter), which is the economically relevant actor regardless of whether the call comes through the liquidity adder or directly: [5](#0-4) 

The swap-side extension has no equivalent stable identity to check; it relies on `sender`, which changes depending on the entry path.

---

### Impact Explanation

When the router is allowlisted (the only way to let curated users trade via the standard periphery), any non-allowlisted user can execute swaps on the curated pool by calling `router.exactInputSingle` or any other router entry point. The pool settles real token transfers against LP reserves. Non-allowlisted users can drain LP assets, extract value at oracle-anchored prices the pool admin intended only for specific counterparties, or violate compliance requirements that the allowlist was meant to enforce. This is a direct loss of LP principal and a broken core pool invariant (curated access).

---

### Likelihood Explanation

The bypass requires the admin to allowlist the router address. This is the natural and expected configuration step for any curated pool that should be accessible via the protocol's standard periphery: without allowlisting the router, even the intended allowlisted users cannot trade through `MetricOmmSimpleRouter`. The admin faces an impossible choice — either allowlist the router (opening the pool to everyone) or do not (blocking allowlisted users from using the router). Any pool operator who follows the natural setup path will trigger the bypass.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end-user identity, not the direct caller of the pool. Two viable approaches:

1. **Extension-data identity forwarding:** Require the router to encode the originating user address in `extensionData` for the first hop, and have `SwapAllowlistExtension` decode and check that address. The extension should reject calls where the encoded identity does not match a trusted forwarding contract.

2. **Separate `originator` parameter:** Add an explicit `originator` field to the pool's `swap` signature (analogous to `owner` in `addLiquidity`) that the router populates with `msg.sender` before calling the pool. The extension then checks `allowedSwapper[pool][originator]` instead of `allowedSwapper[pool][sender]`.

Either approach must ensure the originator field cannot be spoofed by an untrusted caller.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls swapExt.setAllowedToSwap(pool, router, true)   // allowlist the router
  admin calls swapExt.setAllowedToSwap(pool, alice, true)    // allowlist alice
  // bob is NOT allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
    → swap executes, bob receives output tokens
    → allowlist completely bypassed
```

The `SwapAllowlistExtension` emits no event and returns the correct selector, so the bypass is silent. Bob's swap settles against LP reserves at the oracle-anchored price, with no indication that the allowlist was circumvented.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
