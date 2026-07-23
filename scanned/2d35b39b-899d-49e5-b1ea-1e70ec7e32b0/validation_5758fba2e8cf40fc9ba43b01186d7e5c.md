The call chain is fully traceable. Here is the exact analysis:

**Step 1 — What `sender` does the pool pass to `beforeSwap`?**

In `MetricOmmPool.swap`, the `sender` forwarded to `_beforeSwap` is always `msg.sender` of the pool's `swap` function: [1](#0-0) 

**Step 2 — What does the extension check?**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the caller of the extension) and `sender` is the value passed from the pool: [2](#0-1) 

**Step 3 — What does the router pass as `msg.sender` to the pool?**

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls the pool, the pool's `msg.sender` is the **router contract address**, not the end user: [3](#0-2) 

**Result of the identity shift:**

| Entrypoint | `sender` seen by `beforeSwap` | Allowlist key checked |
|---|---|---|
| Direct `pool.swap()` | actual user | `allowedSwapper[pool][user]` |
| `router.exactInputSingle()` | router address | `allowedSwapper[pool][router]` |

This produces two mutually exclusive failure modes for a pool that uses `SwapAllowlistExtension` with per-user entries:

1. **Router not allowlisted** → all router-mediated swaps revert with `NotAllowedToSwap`, even for allowlisted users. The supported periphery path is broken.
2. **Router allowlisted** (the only way to make router swaps work) → `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of whether the actual end user is on the allowlist. Any unprivileged user bypasses the per-user gate by routing through `MetricOmmSimpleRouter`.

There is no configuration that simultaneously allows router-mediated swaps and enforces per-user allowlist policy. The invariant stated in the question — "A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" — is structurally broken.

---

### Title
Router-mediated swaps replace the real swapper identity with the router address in `SwapAllowlistExtension.beforeSwap`, allowing any user to bypass per-user allowlists — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap`. When the swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. Any pool that allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user of that router.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension. [4](#0-3) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender (pool)][sender]`. [5](#0-4) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool level. [6](#0-5) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [7](#0-6) 

A pool admin who wants router-mediated swaps to work must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the per-user check is bypassed for every caller of the router.

### Impact Explanation
Any user who is not individually allowlisted can execute swaps on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant). The allowlist curation — the primary access-control mechanism of `SwapAllowlistExtension` — is rendered ineffective for all router-mediated paths. This constitutes a broken core pool functionality (allowlist enforcement) and enables disallowed users to trade, which is a direct curation failure.

### Likelihood Explanation
The router is the canonical, documented periphery path for end users. Any pool operator who deploys `SwapAllowlistExtension` and also wants users to be able to use the router will naturally allowlist the router address, triggering the bypass. The two-step setup (deploy allowlist extension, allowlist router) is the expected production configuration.

### Recommendation
The extension must resolve the true end-user identity rather than trusting the raw `sender` argument. Two approaches:

1. **Pass `msg.sender` through the router as `extensionData`** and have the extension verify it against a router-signed payload (requires router cooperation and signature verification).
2. **Require the router to pass the original caller in `extensionData`** and have `SwapAllowlistExtension` decode and check that address when `sender` is a known router. The extension would need a registry of trusted routers.
3. **Architectural fix**: change `MetricOmmPool.swap` to accept an explicit `swapper` parameter (separate from `msg.sender`) that the router populates with `msg.sender` at the router level, and have the extension check that field.

### Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
3. Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
   → pool.swap() is called with msg.sender = router
   → beforeSwap(sender=router, ...) is called
   → allowedSwapper[pool][router] == true  → passes
   → Bob's swap executes successfully despite not being on the allowlist
5. Bob (not allowlisted) calls pool.swap() directly
   → beforeSwap(sender=bob, ...) is called
   → allowedSwapper[pool][bob] == false → reverts NotAllowedToSwap
```
The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
