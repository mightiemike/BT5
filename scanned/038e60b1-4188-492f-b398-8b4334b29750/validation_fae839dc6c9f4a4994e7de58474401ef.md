### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router address (a natural step to enable standard-interface usage), every user on the network can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Pool-side sender binding**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

**Extension check**

`SwapAllowlistExtension.beforeSwap` uses that `sender` value keyed against `msg.sender` (the pool) to decide whether the swap is permitted: [2](#0-1) 

**Router always appears as sender**

In every router entry point — `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` — the router calls `pool.swap()` directly. The pool therefore sees `msg.sender = router` and passes the router address as `sender` to the extension for every hop: [3](#0-2) [4](#0-3) 

The router never forwards the original `msg.sender` as the `sender` argument to the pool; it only stores it as the payer in transient callback context. The extension never sees the original user.

**Resulting dilemma for pool admins**

A pool admin who configures `SwapAllowlistExtension` faces an inescapable choice:

| Configuration | Effect |
|---|---|
| Router NOT allowlisted | Allowlisted users cannot use the router at all; they must call the pool directly |
| Router IS allowlisted | Every user on the network can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool admin who allowlists the router (the natural action to enable standard-interface usage) inadvertently opens the pool to all users. Any non-allowlisted address can execute swaps on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` or any other router entry point. This breaks the core access-control invariant of the extension: unauthorized users can drain LP positions at oracle-derived prices, extract value from pools intended for restricted counterparties, or violate regulatory/compliance restrictions the pool admin intended to enforce.

---

### Likelihood Explanation

The `SwapAllowlistExtension` documentation states it "Gates `swap` by swapper address, per pool," implying individual-user gating. A pool admin who reads this and then allowlists the router to enable normal user flows will not realize they have opened the gate to everyone. This is a predictable misconfiguration on any curated pool that also wants to support the standard periphery router.

---

### Recommendation

The extension must resolve the original user identity rather than the immediate caller. Two approaches:

1. **Pass original sender through the router**: Modify `MetricOmmSimpleRouter` to forward `msg.sender` as the `sender` argument to `pool.swap()` instead of relying on the pool's `msg.sender`. This requires a pool interface change or a dedicated "sender override" field.

2. **Check both sender and a caller-provided identity**: Allow the extension to accept a signed or verified original-user identity from `extensionData`, and verify it against the allowlist instead of (or in addition to) the raw `sender`.

Until resolved, pool admins should be warned that allowlisting the router is equivalent to disabling the allowlist entirely.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for standard UX

Attack (executed by bob, who is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: curated_pool,
       recipient: bob,
       ...
     })
  2. Router calls curated_pool.swap(bob, ...)
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; bob receives tokens from the curated pool

Result: bob, a non-allowlisted user, successfully swaps on a pool
        that was configured to restrict access to alice only.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-113)
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
