### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `MetricOmmPool.swap`. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router contract address. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed on an allowlisted pool), every user — including those the allowlist was designed to exclude — can bypass the guard by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the first argument forwarded to `_beforeSwap` is `msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` argument in the ABI-encoded call to every configured extension: [2](#0-1) 

**Step 2 — The extension checks `sender`, which is the router, not the end user.**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the router) and checks it against the per-pool allowlist: [3](#0-2) 

**Step 3 — The router calls `pool.swap` directly, making itself the `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` with no mechanism to forward the original caller's identity: [4](#0-3) 

**The structural mismatch:** The allowlist is keyed by `[pool][sender]`. For direct swaps, `sender = end user` — the check is correct. For router-mediated swaps, `sender = router` — the check is against the router address, not the end user. A pool admin who wants to support router-mediated swaps for their allowlisted users must add `allowedSwapper[pool][router] = true`. Once the router is allowlisted, every user — allowlisted or not — can bypass the guard by calling any of the router's public entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

The `SwapAllowlistExtension` documentation states it "Gates `swap` by swapper address, per pool": [5](#0-4) 

The invariant — that only allowlisted swappers can trade — is broken the moment the router is added to the allowlist.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional partners, or whitelisted bots) is fully open to any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can drain LP-owned inventory at oracle prices, extract spread fees, or manipulate the pool's bin cursor — all actions the allowlist was configured to prevent. This constitutes a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants their allowlisted users to benefit from multi-hop routing (the primary use case of `MetricOmmSimpleRouter`) must allowlist the router. There is no alternative path: the router is a public, permissionless contract with no access control of its own. The bypass is therefore reachable by any unprivileged user on every pool that supports router-mediated swaps alongside an allowlist.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the **transport layer** (the router). Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router.
2. **Sender override in the pool interface**: Add an optional `originalSender` field to the swap call that the pool passes to extensions, populated by the router from its own `msg.sender` at entry.

Until one of these is implemented, pool admins must choose between supporting router-mediated swaps and enforcing a meaningful swap allowlist — they cannot have both.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin = pool admin

Step 1 — Admin allowlists user1 for direct swaps:
  admin calls swapExtension.setAllowedToSwap(pool, user1, true)

Step 2 — Admin allowlists the router so user1 can use multi-hop routing:
  admin calls swapExtension.setAllowedToSwap(pool, router, true)

Step 3 — user2 (not allowlisted) bypasses the guard:
  user2 calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; user2 trades on the curated pool without authorization

Invariant violated:
  allowedSwapper[pool][user2] == false, yet user2's swap settles successfully.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
