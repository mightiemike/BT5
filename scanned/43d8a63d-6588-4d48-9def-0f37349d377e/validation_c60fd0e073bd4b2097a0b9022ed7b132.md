### Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool always sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the gate to every user, completely defeating the per-user allowlist.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 — Extension checks `sender` (the direct pool caller), not the end user.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When the router is the caller, `sender = router`.

**Step 3 — Router calls `pool.swap()` as itself.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly; the pool receives `msg.sender = router`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The bypass:**

| Configuration | Direct pool call | Router call |
|---|---|---|
| Alice allowlisted, router **not** allowlisted | Alice ✓ | Alice ✗ (router blocked) |
| Alice allowlisted, router **allowlisted** | Alice ✓ | **Anyone ✓** ← bypass |

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the extension passes for every caller regardless of who the end user is.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified institutions, whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Any unprivileged address can execute swaps on the pool by calling any of the four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). This is an admin-boundary break: the pool admin's access-control policy is bypassed by an unprivileged path through a supported periphery contract.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. A pool admin who configures a swap allowlist and also wants their allowlisted users to be able to use the router will naturally allowlist the router address — the only way to make router-mediated swaps work for legitimate users. This is a predictable operational step that silently removes all per-user gating. The bypass requires no special privileges, no flash loans, and no multi-transaction setup: a single router call suffices.

---

### Recommendation

The extension must gate the **end user**, not the direct pool caller. Two viable approaches:

1. **Forward the originating user in `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before calling `pool.swap()`. The `SwapAllowlistExtension` decodes and checks that address instead of (or in addition to) `sender`. This requires a coordinated encoding convention between the router and the extension.

2. **Check both `sender` and a decoded user**: The extension checks `allowedSwapper[pool][sender]` as today, but also accepts a signed or encoded user address from `extensionData` when `sender` is a known router, verifying the inner user is allowlisted.

Either approach must be applied consistently across all router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and the multi-hop callback path in `_exactOutputIterateCallback`.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension wired to beforeSwap.
2. Admin: swapExtension.setAllowedToSwap(pool, alice, true)
          // Alice is the only intended swapper
3. Admin: swapExtension.setAllowedToSwap(pool, router, true)
          // Router allowlisted so Alice can use the router UI
4. Mallory (not allowlisted) calls:
          router.exactInputSingle({pool: pool, recipient: mallory, ...})
5. Router executes: pool.swap(mallory, ...) with msg.sender = router
6. Pool calls: _beforeSwap(router, mallory, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes
8. Mallory's swap executes successfully on the curated pool.
``` [5](#0-4) [1](#0-0) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
