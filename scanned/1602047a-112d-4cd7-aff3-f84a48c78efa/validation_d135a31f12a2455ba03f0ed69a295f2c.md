### Title
SwapAllowlistExtension gates on the router address instead of the actual user, allowing complete allowlist bypass through MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted (required for any user to use the router on this pool), the allowlist is completely bypassed for all users.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` faithfully forwards that value:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual user's address is stored only in the router's transient callback context (`_getPayer()`) and is never surfaced to the pool or the extension.

The same wrong-actor binding applies to all four router entry points: `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Contrast with `DepositAllowlistExtension`:** that extension checks the `owner` argument (the position owner explicitly passed by the caller), which correctly identifies the economic actor regardless of intermediary. The swap extension has no equivalent — it can only see the immediate caller of `pool.swap()`.

---

### Impact Explanation

Any user can bypass the swap allowlist on a curated pool by calling any `MetricOmmSimpleRouter` entry point. Once the router is allowlisted (the only way for any user to use the router on this pool), the allowlist is open to everyone. Concrete consequences:

- Unauthorized users trade on pools intended for specific counterparties (e.g., KYC-gated, institutional-only, or partner-restricted pools).
- Adversarial actors can extract value from LP positions on pools designed to trade only against trusted counterparties.
- The pool admin's curation policy is silently nullified without any on-chain signal.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router. However, allowlisting the router is the natural and expected step for any pool that wants to support the standard periphery UX. A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to specific users and also wants those users to use the router will inevitably create this bypass, because there is no mechanism to allowlist "router + specific originator" as a combined identity.

---

### Recommendation

**Short term:** Document explicitly that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter`. Pools using this extension must require direct `pool.swap()` calls; the router must not be allowlisted.

**Long term:** Introduce an originator field that the router passes through `extensionData` and that the extension verifies (e.g., a signed originator address). Alternatively, redesign the extension to gate on a combination of `(sender, extensionData-originator)` so the router can attest the real user without the pool needing to trust arbitrary calldata.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only user A is intended to swap.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` — necessary for user A to use the router.
4. Non-allowlisted user B calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. User B's swap executes on the allowlisted pool, bypassing the intended restriction. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
