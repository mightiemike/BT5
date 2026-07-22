### Title
`SwapAllowlistExtension` checks the router's address instead of the original user's address, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the original user's address. If the pool admin allowlists the router (the natural action to enable router-mediated swaps for permitted users), every user — including those explicitly not allowlisted — can bypass the gate by routing through the router.

---

### Finding Description

**Root cause — wrong actor bound in the hook:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the argument forwarded by the pool. The pool always forwards its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

**The router substitution:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [3](#0-2) 

When this call reaches the pool, `msg.sender` = router. The pool therefore passes `sender = router` to `_beforeSwap`, and the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][original_user]`. The original user's identity is never consulted.

**The bypass path:**

A pool admin who wants to allow specific EOAs to trade through the router must allowlist the router itself (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the router, regardless of whether that caller is individually permitted. The allowlist is completely defeated for the router path.

The same structural flaw exists in `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap()` as `msg.sender = router`. [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. An unauthorized user can drain LP-owned token inventory at oracle prices, extract value from a pool that was designed to be closed to them, or front-run allowlisted participants on a pool that was supposed to be private. This is a direct loss of LP principal and a broken core pool invariant (the allowlist).

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router — a natural, expected action for any curated pool that intends to support the standard periphery. The admin has no on-chain signal that doing so opens the gate to all users; the `isAllowedToSwap` view function returns `true` for the router and gives no indication that the original user is not checked. The bypass is reachable by any unprivileged user with no special setup beyond calling the public router.

---

### Recommendation

Pass the original user's address through the extension rather than the immediate pool caller. Two concrete options:

1. **Encode the original user in `extensionData`**: the router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks that address. This requires the extension to trust the router, which must be verified separately.

2. **Dedicated sender field in the hook**: add an `originalSender` field to the `beforeSwap` interface that the pool populates from a trusted periphery registry, so the extension always sees the economic actor regardless of routing depth.

Until fixed, document that `SwapAllowlistExtension` only enforces the allowlist for direct `pool.swap()` calls and must not be used with the public router on curated pools.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is permitted
  admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
  LP adds liquidity

Attack (Eve, not allowlisted):
  1. Eve calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=eve, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension evaluates: allowedSwapper[pool][router] → true  ✓
  5. Swap executes; Eve receives tokens from the curated pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed
```

The corrupted value is `allowedSwapper[pool][router]` being evaluated in place of `allowedSwapper[pool][eve]`. The pool admin's intended gate is silently open to every user of the public router. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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
