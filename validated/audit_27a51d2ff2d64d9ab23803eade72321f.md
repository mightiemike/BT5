### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. A pool admin who allowlists the router to support router-based swaps for their curated pool inadvertently opens the pool to **all** users, completely defeating the per-user allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded from `MetricOmmPool.swap`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` with `msg.sender = router`:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
``` [3](#0-2) 

So the extension sees `sender = router address`, not the actual user. The allowlist check becomes `allowedSwapper[pool][router]`.

**The trap**: A pool admin who wants their allowlisted users to be able to use the standard router must call `setAllowedToSwap(pool, router, true)`. The moment they do, **every** user — including those explicitly not on the allowlist — can route through `MetricOmmSimpleRouter` and bypass the restriction entirely.

This is structurally different from `DepositAllowlistExtension`, which correctly checks `owner` (the actual position owner, explicitly passed through the call chain and preserved by `MetricOmmPoolLiquidityAdder`):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [4](#0-3) 

The swap extension has no analogous "actual user" field — `sender` is the immediate caller of `pool.swap`, and `recipient` (the second argument, ignored by the extension) is the output-token destination, which can be set to any address by the router.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise approved addresses is rendered ineffective the moment the pool admin allowlists the router (a natural and expected operational step). Any unprivileged user can then call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and execute swaps against the pool, bypassing the curation policy entirely. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's configured allowlist is silently circumvented by a valid public entrypoint.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins operating curated pools will routinely allowlist the router to give their approved users a standard UX. The bypass is therefore triggered by a normal, expected operational configuration, not an exotic or adversarial setup. Any user aware of the router address can exploit it.

---

### Recommendation

The `beforeSwap` hook should gate on the **actual initiating user**, not the immediate caller of `pool.swap`. Two options:

1. **Mirror the deposit pattern**: Introduce an explicit `swapper` identity field (analogous to `owner` in `addLiquidity`) that the router populates with `msg.sender` before calling the pool, and forward it through `extensionData` or a dedicated parameter.

2. **Short-term mitigation**: Document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and that pool admins must never allowlist the router address; instead, users must call `pool.swap` directly. This is a severe UX restriction and not a real fix.

The correct long-term fix is to pass the originating user's address through the extension hook, consistent with how `owner` is handled in the liquidity path.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin allowlists alice: setAllowedToSwap(pool, alice, true)
  - Pool admin allowlists router: setAllowedToSwap(pool, router, true)
    (so alice can use the router)

Attack (bob, not on allowlist):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: bob,
       zeroForOne: false,
       amountIn: X,
       ...
     })
  2. Router calls pool.swap(bob, false, X, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. Extension checks allowedSwapper[pool][router] → true (admin allowlisted router)
  5. Swap executes successfully — bob bypassed the allowlist

Result: bob, who is not on the allowlist, successfully swaps on the curated pool.
``` [5](#0-4) [6](#0-5) [2](#0-1)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
