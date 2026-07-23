### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When any user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates whether the **router** is allowlisted rather than the actual user. A pool admin who allowlists the router (the natural step to enable router-based swaps for legitimate users) inadvertently opens the gate to every address on-chain, completely defeating the per-user curation the extension was deployed to enforce.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller), and `sender` is whatever address the pool forwarded. [1](#0-0) 

**What the pool forwards as `sender`:**

`MetricOmmPool.swap` passes `msg.sender` (the direct caller of the pool) as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

**What the router passes to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router itself `msg.sender` of that call. The actual user's address is stored only in transient storage for the payment callback and is never forwarded to the pool or the extension:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The bypass path:**

When a user routes through the router, the extension evaluates `allowedSwapper[pool][router_address]`. If the pool admin has allowlisted the router (the only way to make router-based swaps work for any user), the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: only specific addresses (e.g., KYC-verified counterparties, institutional traders, or protocol-controlled bots) are supposed to trade. Once the pool admin allowlists the router to support standard periphery usage, the curation boundary collapses entirely. Any address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps in the pool. This is a direct loss of the access-control invariant the extension was deployed to enforce, and it exposes LP funds to trades from actors the pool admin explicitly intended to exclude.

Severity: **High** — broken core pool functionality (allowlist guard) with direct fund-impact consequence (unauthorized actors drain LP value through unrestricted swaps).

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is not a malicious or unusual action; it is the expected operational step for any curated pool that also wants to support the standard periphery. The admin has no other option: without allowlisting the router, even individually allowlisted users cannot use the router. The bypass is therefore a near-certain consequence of any curated pool that supports router-based swaps.

---

### Recommendation

The extension must evaluate the **economic actor** (the end user), not the intermediary. Two concrete fixes:

1. **Router forwards user identity via `extensionData`:** The router encodes `msg.sender` into `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router. This requires a convention between the router and the extension.

2. **Pool exposes an explicit `swapper` parameter:** Add a `swapper` field to `pool.swap()` (separate from `msg.sender`) that the router populates with the originating user. The extension then checks `swapper` instead of `sender`. This is the cleanest fix but requires a core interface change.

Until fixed, pool admins must not allowlist the router address in `SwapAllowlistExtension` and must instruct users to call the pool directly, which eliminates multi-hop routing.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls extension.setAllowedToSwap(pool, alice, true)
   → allowedSwapper[pool][alice] = true
3. Pool admin calls extension.setAllowedToSwap(pool, router, true)
   → allowedSwapper[pool][router] = true
   (necessary so that alice can use the router)

Attack
──────
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})

5. Router executes:
       pool.swap(recipient, zeroForOne, amount, priceLimit, "", "")
   with msg.sender = router

6. Pool calls:
       _beforeSwap(sender=router, ...)
   which calls:
       extension.beforeSwap(sender=router, ...)

7. Extension evaluates:
       allowedSwapper[pool][router] == true  →  passes

8. bob's swap executes successfully in a pool he was never authorized to trade in.

Result
──────
bob, a non-allowlisted address, executes a live swap against LP funds in a
curated pool, extracting value that the pool admin intended to restrict to
allowlisted counterparties only.
```

The root cause is at: [5](#0-4) 

triggered through: [2](#0-1) 

via the router entry point: [6](#0-5)

### Citations

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
