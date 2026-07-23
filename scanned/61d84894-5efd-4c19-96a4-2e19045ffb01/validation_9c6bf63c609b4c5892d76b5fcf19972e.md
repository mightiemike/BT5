### Title
`SwapAllowlistExtension` gates the router's address instead of the end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` = router address. The allowlist check therefore gates the router's identity, not the actual end-user's identity. If the pool admin allowlists the router to support router-mediated swaps for legitimate users, every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry-point) calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

From the pool's perspective `msg.sender` = router address, so `sender` forwarded to the extension = router address.

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), and `sender` is the router (wrong actor). The allowlist lookup is therefore `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router address to support router-mediated swaps for legitimate users, the check passes for **every** caller who routes through the router, regardless of their actual identity.

The router provides no mechanism to forward the original EOA's address to the extension; `extensionData` is forwarded verbatim but `SwapAllowlistExtension` ignores it entirely.

---

### Impact Explanation

**Direct loss of curation policy / unauthorized swap execution on curated pools.**

A pool configured with `SwapAllowlistExtension` is a curated pool: only allowlisted addresses may trade. Once the router is allowlisted (a necessary step for any legitimate user who wants to use the standard periphery), the allowlist is completely open to any unprivileged caller. The attacker can execute swaps against the pool's liquidity at the oracle-derived bid/ask price, extracting value from LP positions that were intended to be protected behind the allowlist. This is a direct loss of LP principal and a broken core pool invariant (the curation boundary the pool admin configured).

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the standard, documented periphery swap path. Any pool admin who wants allowlisted users to be able to use the router (the normal UX) must add the router to the allowlist. The moment they do, the allowlist is bypassed for all users. The attacker needs no special privilege, no malicious setup, and no non-standard token — only a call to a public router function.

---

### Recommendation

The allowlist must gate the **original end-user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` (the EOA) into `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of (or in addition to) `sender`.

2. **Check `sender` AND the decoded originator**: The extension can require that either the direct `sender` is allowlisted (for direct pool calls) or the decoded originator from `extensionData` is allowlisted (for router calls), with the pool admin choosing the policy.

A minimal diff for the extension side:

```diff
 function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata extensionData)
     external
     view
     override
     returns (bytes4)
 {
+    address actor = extensionData.length == 20
+        ? address(bytes20(extensionData))
+        : sender;
-    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][actor]) {
         revert IMetricOmmPoolActions.NotAllowedToSwap();
     }
     return IMetricOmmExtensions.beforeSwap.selector;
 }
```

The router must be updated to encode `msg.sender` into `extensionData` before forwarding to the pool.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so that allowlisted users can trade via the router.
3. `attacker` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(recipient, ..., extensionData)` with `msg.sender = router`.
5. The pool calls `extension.beforeSwap(router, ...)`.
6. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. The swap executes at the oracle bid/ask price; the attacker receives output tokens.
8. The allowlist is fully bypassed without any privileged action. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
