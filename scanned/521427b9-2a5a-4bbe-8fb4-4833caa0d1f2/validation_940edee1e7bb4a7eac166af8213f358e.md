### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address the pool received as `msg.sender` — i.e., the router contract when a user routes through `MetricOmmSimpleRouter`. Because the router is a single shared address, any user who routes through it is checked against the router's allowlist entry, not their own. A pool admin who allowlists the router (the natural step to let their curated users use the standard periphery) simultaneously opens the gate to every non-allowlisted user on the network.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to the extension.**

In `MetricOmmPool.swap()`, the `sender` argument forwarded to `_beforeSwap` is always `msg.sender`: [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly with no forwarding of the original caller: [2](#0-1) 

So `msg.sender` seen by the pool — and therefore `sender` seen by every extension — is the **router's address**, not the end user's address.

**Step 2 — Extension checks the router address, not the end user.**

`SwapAllowlistExtension.beforeSwap` checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct), and `sender` is the router address (wrong actor). The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

**Step 3 — The pool admin's only viable configuration opens the bypass.**

A pool admin who wants their allowlisted users to be able to use the standard router must call `setAllowedToSwap(pool, router, true)`. The moment they do, `allowedSwapper[pool][router] = true`, and the check passes for **every** caller of the router regardless of their own allowlist status. [4](#0-3) 

The same structural problem exists for `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly without forwarding the original caller. [5](#0-4) 

---

### Impact Explanation

Any non-allowlisted user can trade on a curated pool that was intended to be restricted (e.g., KYC-only, institutional-only, or whitelist-gated). The allowlist extension — the sole access-control mechanism for swap gating — is silently bypassed. This is a direct policy failure with fund-level consequences: non-permitted users can drain liquidity from curated pools at oracle-quoted prices, and the pool admin has no on-chain recourse short of removing the router from the allowlist (which simultaneously breaks the experience for legitimate users).

---

### Likelihood Explanation

- The router is the standard, documented periphery entry point. Pool admins are expected to allowlist it.
- The bypass requires zero privileged access: any EOA calls `router.exactInputSingle(...)`.
- No special timing, price manipulation, or multi-step setup is needed.
- The flaw is structural and present on every pool that uses `SwapAllowlistExtension` with the router allowlisted.

---

### Recommendation

The extension must gate on the **economic actor** (the end user), not the intermediary. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Dedicated router that forwards the original caller as a verified field**: Add a `caller` parameter to the pool's `swap` signature (or a separate authenticated path) so the pool can pass the true originator to extensions without relying on `msg.sender`.

The current design where `sender = msg.sender` (the router) is fundamentally incompatible with per-user allowlist enforcement through the standard periphery.

---

### Proof of Concept

```
Setup:
  pool = curated pool with SwapAllowlistExtension configured
  alice = allowlisted user
  bob = non-allowlisted user
  router = MetricOmmSimpleRouter (allowlisted so alice can use it)

Attack:
  1. Pool admin calls setAllowedToSwap(pool, alice, true)
  2. Pool admin calls setAllowedToSwap(pool, router, true)  ← required for alice to use router
  3. bob calls router.exactInputSingle({pool: pool, ...})
     → router calls pool.swap(...)
     → pool passes sender = router to _beforeSwap
     → SwapAllowlistExtension checks allowedSwapper[pool][router] = true
     → swap executes for bob despite bob not being allowlisted

Result: bob trades on a pool he was explicitly excluded from.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
