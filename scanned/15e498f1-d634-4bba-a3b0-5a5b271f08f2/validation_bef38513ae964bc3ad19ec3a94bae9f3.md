### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool always passes `msg.sender` of the `swap` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. A pool admin who allowlists the router (the only way to permit router-mediated swaps) simultaneously opens the gate to every user on-chain, completely defeating the per-user curation the extension was deployed to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
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

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist keyed by the pool address (`msg.sender` inside the extension, which is the pool):

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` at the pool:

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

The pool therefore passes the router's address as `sender` to the extension. The extension never sees the originating user's address.

This creates an inescapable dilemma for any pool admin who deploys `SwapAllowlistExtension`:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router; they must call the pool directly, breaking the standard UX |
| **Allowlist the router** | Every user on-chain can swap through the router; the per-user allowlist is completely bypassed |

---

### Impact Explanation

Any user who is explicitly **not** on the allowlist can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`. The pool admin's intent to restrict trading to a specific set of counterparties is silently voided. Depending on the pool's purpose (e.g., institutional-only liquidity, KYC-gated pools, or pools with concentrated oracle-anchored pricing that should only be accessible to trusted market makers), this allows arbitrary users to drain LP value at oracle-derived prices that were never meant to be publicly accessible. The loss is direct and repeatable: every swap executed by a disallowed user extracts value from LP positions at prices the LPs agreed to provide only to trusted counterparties.

---

### Likelihood Explanation

The router is the canonical user-facing entry point documented and deployed alongside the core pool. Any pool admin who wants their allowlisted users to have a normal trading experience must allowlist the router. The bypass requires no special privileges, no flash loans, and no multi-block setup — a single `exactInputSingle` call through the router suffices. Likelihood is high.

---

### Recommendation

The extension must resolve the originating user rather than the immediate caller. Two sound approaches:

1. **Pass the originating user explicitly**: Add a `payer` or `originator` field to the `beforeSwap` hook arguments (or to `extensionData`) that the router populates with `msg.sender` before calling the pool. The extension then checks that field instead of `sender`.

2. **Check `recipient` or require direct-pool-only swaps**: For pools that must enforce per-user allowlists, document that the router cannot be used and enforce this by checking that `sender == recipient` or by verifying `sender` is not a known router address.

The simplest production fix is to have the router encode the originating user in `extensionData` and have the extension decode and check that address, falling back to `sender` when the field is absent (for direct pool calls).

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls extension.setAllowedToSwap(pool, router, true)
    // admin must do this so that any allowlisted user can use the router
  admin does NOT call extension.setAllowedToSwap(pool, alice, true)
    // alice is explicitly excluded

Attack:
  alice calls router.exactInputSingle({
      pool:       pool,
      recipient:  alice,
      zeroForOne: true,
      amountIn:   X,
      ...
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(alice, true, X, ...) [msg.sender = router]
      → _beforeSwap(router, alice, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, alice receives tokens

Result:
  alice, who is not on the allowlist, successfully swaps on the curated pool.
  The allowlist check passed because it evaluated the router's address, not alice's.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
