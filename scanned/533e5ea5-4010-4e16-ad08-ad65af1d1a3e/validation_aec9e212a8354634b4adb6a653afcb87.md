### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist via the Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end-user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for curated pools), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking the `sender` parameter against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the address the pool forwarded: [1](#0-0) 

The pool populates `sender` with its own `msg.sender` (the direct caller of `swap`), as confirmed by the interface error annotation "Swap allowlist rejected `msg.sender`": [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this `sender` verbatim to every configured extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same applies to `exactOutputSingle`, `exactInput`, and `exactOutput`. In every case the pool receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`.

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the router, regardless of whether that caller is individually allowlisted. The per-user gate is silently voided.

---

### Impact Explanation

Any user who is **not** individually allowlisted can swap on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The allowlist — the sole access-control mechanism for curated pools — is completely bypassed. This is an admin-boundary break: an unprivileged actor reaches a swap path the pool admin explicitly intended to restrict. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, or private LP pools), this can expose LP funds to unauthorized counterparties and violate the pool's economic invariants.

---

### Likelihood Explanation

The trigger requires two conditions:

1. A pool is deployed with `SwapAllowlistExtension` configured as a `beforeSwap` hook (a supported, documented production configuration).
2. The pool admin allowlists the router address — a natural and expected action for any curated pool that intends to support standard periphery tooling.

Both conditions are routine operational steps. No malicious setup or non-standard token is required. Any unprivileged user can then exploit the bypass by calling the public router.

---

### Recommendation

The `SwapAllowlistExtension` should gate the **end-user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` already stores the original `msg.sender` in transient storage as the payer. The pool's `swap` signature could accept an explicit `originator` argument, or the router could encode the real user in `extensionData` for the extension to decode and verify.

2. **Check `sender` only when it is not a known router**: The extension could maintain a registry of trusted routers and, when `sender` is a router, require the real user identity to be supplied in `extensionData` and verified there.

The simplest safe fix is to not allowlist the router at all and require allowlisted users to call the pool directly — but this should be explicitly documented as a constraint, and the router should revert with a clear error when used against an allowlist-gated pool.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls swapExtension.setAllowedToSwap(pool, router, true)   // allowlist the router
  admin calls swapExtension.setAllowedToSwap(pool, alice, true)    // alice is individually allowed
  bob is NOT individually allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   // msg.sender to pool = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
    → swap executes successfully for bob

Direct call (control):
  bob calls pool.swap(...) directly
    → pool calls _beforeSwap(sender=bob, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][bob] → false
    → revert NotAllowedToSwap ✓

Result: bob bypasses the per-user allowlist by routing through the router.
``` [1](#0-0) [5](#0-4) [3](#0-2)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
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
