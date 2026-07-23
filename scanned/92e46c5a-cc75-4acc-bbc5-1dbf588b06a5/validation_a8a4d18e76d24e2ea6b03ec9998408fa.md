### Title
`SwapAllowlistExtension` gates on the pool's immediate caller (`sender`) rather than the end user, allowing any non-allowlisted user to bypass a curated pool's swap restriction via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the router address is allowlisted (a natural operational choice for a "trusted" periphery), every user who routes through it bypasses the per-user allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `sender` against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` at the pool is the **router contract**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][endUser]`. If the pool admin has allowlisted the router (a natural step when deploying a curated pool that is also meant to be accessible via the standard periphery), every user who routes through it is implicitly allowlisted, regardless of their individual status.

The same structural mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput` paths in the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a vetted set of addresses can be fully bypassed by any user who calls through `MetricOmmSimpleRouter`, provided the router is allowlisted. The curated pool's protection boundary collapses to zero: disallowed users can execute swaps, drain liquidity, and interact with the pool as if they were allowlisted. This is a direct loss of the curation guarantee and can result in unauthorized capital flows out of the pool.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented user-facing entry point for swaps. A pool admin who deploys a curated pool and also wants standard router access will naturally allowlist the router address — the admin has no on-chain signal that doing so voids the per-user allowlist. The bypass requires no special privileges, no flash loans, and no unusual token behavior: any user with a standard ERC-20 approval to the router can trigger it.

---

### Recommendation

The allowlist must key authorization to the **end user**, not the immediate pool caller. Two complementary fixes:

1. **Pass the real payer through `extensionData`**: The router already stores the real payer in transient storage (`_getPayer()`). It should encode the payer address into `extensionData` so the extension can verify the actual user.

2. **Check `recipient` or require direct calls**: Alternatively, `SwapAllowlistExtension` can be changed to check `recipient` (the address receiving tokens) instead of `sender`, since the recipient is the economically relevant party and cannot be spoofed by the router. For deposit allowlists the analogous fix is to check `owner` rather than `sender`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin allowlists the router
  allowedSwapper[pool][alice]  = false  // alice is NOT individually allowlisted

Attack:
  alice calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient=alice, ...)
    → pool.msg.sender = router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → no revert
  alice's swap executes successfully despite not being allowlisted
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
