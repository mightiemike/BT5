### Title
SwapAllowlistExtension Checks Router Address Instead of End-User Identity, Allowing Any User to Bypass Per-User Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When `MetricOmmSimpleRouter` is the caller, `msg.sender` at the pool level is the router contract, so `sender` forwarded to the extension is the router address — not the actual end user. Any user can bypass a per-user allowlist on a curated pool by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every `_beforeSwap` hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool): [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap(...)`, `msg.sender` at the pool is the **router contract**, not the originating EOA: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. There is no mechanism in the pool or router to thread the originating user's address through to the extension.

This creates an irreconcilable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use the router at all — broken UX |
| Yes | **Every user** can bypass the per-user allowlist via the router |

The router is the primary user-facing interface documented and deployed for the protocol. Pool admins who want their allowlisted users to access the router must allowlist it, which silently opens the pool to all users.

---

### Impact Explanation

A curated pool (e.g., KYC-gated, institutional, or compliance-restricted) configured with `SwapAllowlistExtension` and a per-user allowlist can be freely traded against by any unprivileged address through `MetricOmmSimpleRouter`. LP funds in the pool are exposed to unrestricted swaps, violating the pool's intended access policy. This is a direct loss-of-access-control impact: the pool's LP assets are at risk from actors the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it.
- Pool admins who deploy `SwapAllowlistExtension` and want their allowlisted users to use the router (the standard UX path) will allowlist the router, triggering the bypass.
- No special privileges, flash loans, or unusual token behavior are required. A single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate the **originating user**, not the immediate pool caller. Two viable approaches:

1. **Pass originating user through the router**: Add an `originSender` field to the pool's swap interface or extension data, populated by the router as `msg.sender` before calling the pool. The extension reads this field instead of the raw `sender` argument.

2. **Reject router-mediated swaps on allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at the extension level by reverting when `sender != tx.origin` (with appropriate caveats for smart-contract wallets).

The cleanest fix is option 1: the router stores the originating user in transient storage (as it already does for callback context) and the extension reads it via a standardized slot.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, ...)
     → msg.sender at pool = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router] == true  ✓
     → swap proceeds for bob despite bob not being allowlisted

Result: bob successfully swaps on a pool that should have blocked him.
        If admin does NOT allowlist the router, alice also cannot use the router.
        There is no configuration that correctly enforces per-user allowlisting
        while also permitting router access.
``` [2](#0-1) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
