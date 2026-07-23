### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, making the allowlist either universally bypassable or broken for all router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the value of `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. The extension therefore gates the router's address, not the actual swapper. This creates an irreconcilable dilemma for any pool admin who deploys this extension on a pool that is also meant to be used through the standard periphery router.

---

### Finding Description

**Root cause — wrong actor bound to `sender`**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = whoever called `pool.swap()`.

**The router is the pool's `msg.sender`, not the end-user**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The router is `msg.sender` of this call. The pool therefore passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`.

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops through `_exactOutputIterateCallback`).

**The two failure modes**

| Admin configuration | Outcome |
|---|---|
| Router **not** allowlisted | Every allowlisted user who routes through the router gets `NotAllowedToSwap`. The standard periphery path is broken for all curated-pool users. |
| Router **allowlisted** (to fix the above) | Every non-allowlisted user can bypass the allowlist by calling `exactInputSingle` / `exactInput` / `exactOutput` through the router. The allowlist is nullified. |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

**Allowlist bypass (router allowlisted):** Any non-allowlisted address can trade on a curated pool by routing through `MetricOmmSimpleRouter`. The pool admin's intent to restrict trading to specific counterparties is completely defeated. Depending on the pool's purpose (e.g., institutional-only, compliance-gated, or rate-limited pools), this allows arbitrary users to extract value at oracle-anchored prices that were only meant to be available to approved parties.

**Broken core swap path (router not allowlisted):** Allowlisted users who rely on the standard periphery router — the primary user-facing swap interface — cannot execute swaps. This renders the pool's swap functionality unusable through the supported periphery path, constituting broken core pool functionality.

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects users to interact through `MetricOmmSimpleRouter` (the standard periphery) is affected. The router is the documented, primary swap interface. Pool admins who configure an allowlist will naturally also expect the router to work, making one of the two failure modes inevitable. The bypass path requires only a standard `exactInputSingle` call with no special privileges.

---

### Recommendation

The extension must receive the actual end-user identity, not the intermediary's address. Two approaches:

1. **Pass the original `msg.sender` through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it. This requires a trusted encoding convention.

2. **Check `recipient` instead of `sender` for swap allowlisting:** If the economic intent is to gate who receives output tokens, `recipient` is the correct field. However, for input-side gating, neither `sender` nor `recipient` alone is sufficient when a router is involved.

3. **Structural fix:** Add a dedicated `originalSender` parameter to the `beforeSwap` hook interface, populated by the pool from a trusted transient-storage slot set by the router at entry (analogous to how the router already stores the payer in `_setNextCallbackContext`).

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-based swaps (or alternatively, allowlists Alice but not the router).
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite not being on the allowlist.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
