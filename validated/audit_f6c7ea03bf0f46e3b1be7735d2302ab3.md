Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the originating user. A pool admin who allowlists the router to enable their permissioned users to swap through the standard periphery inadvertently grants every unprivileged address the ability to bypass the per-user allowlist.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)` at line 230–240. The `sender` forwarded to every extension is therefore the **immediate caller of `pool.swap()`**, not the originating EOA. [1](#0-0) 

**Step 2 — Router is `msg.sender` to the pool.**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly, making the router contract `msg.sender` to the pool. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [2](#0-1) 

**Step 3 — Allowlist checks the router, not the user.**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router. The check is therefore `allowedSwapper[pool][router]`, never `allowedSwapper[pool][actualUser]`. [3](#0-2) 

**Step 4 — The forced dilemma.**

A pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to swap through the standard periphery faces two broken options:

| Admin action | Result |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert — allowlisted users cannot use the periphery |
| **Allowlist the router** | `allowedSwapper[pool][router] = true` → every address on-chain can swap through the router, allowlist is void |

The second option is the natural fix an admin would apply, permanently voiding the guard.

## Impact Explanation

Any unprivileged user can swap on a pool intended to be permissioned (KYC-gated, institutional-only, or otherwise restricted) by calling `MetricOmmSimpleRouter.exactInputSingle` or any multi-hop variant. The allowlist guard configured by the pool admin is completely ineffective for all router-mediated swaps once the router is allowlisted. This is a direct admin-boundary break: an unprivileged path bypasses the access control the pool admin configured, allowing unauthorized parties to interact with restricted pools and drain or manipulate pool assets.

## Likelihood Explanation

High. `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Pool admins who deploy a `SwapAllowlistExtension` will inevitably discover that their allowlisted users cannot swap through the router and will allowlist the router address to fix it — at which point the guard is permanently bypassed for all users. No special attacker capability is required beyond calling the public router.

## Recommendation

`SwapAllowlistExtension` must gate on the economic actor, not the immediate caller. Two sound approaches:

1. **Check `sender` against the allowlist only when `sender` is not a trusted router; otherwise decode the real user from `extensionData`.** Require the router to encode `msg.sender` (the real user) into `extensionData` and have the extension decode and verify it against the allowlist.

2. **Gate on `tx.origin` as a secondary check when `sender` is a known router.** Less clean but avoids `extensionData` coupling.

The `setAllowedToSwap` setter and `allowedSwapper` mapping are correct in structure; only the identity being checked in `beforeSwap` needs to change. [4](#0-3) 

## Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Admin calls setAllowedToSwap(pool, router, true)  // "fix" so alice can use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle({
           pool:      pool,
           recipient: bob,
           zeroForOne: true,
           amountIn:  X,
           ...
       })
5. Router calls pool.swap(bob, true, X, ...) — msg.sender to pool = router
6. Pool calls _beforeSwap(router, bob, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  ✓
8. Swap executes successfully for Bob despite Bob not being allowlisted.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
