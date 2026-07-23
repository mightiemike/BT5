Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Any Unprivileged Caller to Bypass a Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap`, so the extension checks the router's allowlist status rather than the actual end user's. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged address can bypass the allowlist by calling through the router with a single `exactInputSingle` call.

## Finding Description

**Root cause — pool passes `msg.sender` (the router) as `sender` to the extension:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 230–231. [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap`, the router is `msg.sender` of that call, so `sender` forwarded to the extension is the router address, not the end user. [2](#0-1) 

**Extension checks the router, not the user:**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — the end user's address is never seen by the extension. [3](#0-2) 

**End user identity is stored only for payment, never forwarded to the pool:**

The real caller (`msg.sender` of `exactInputSingle`) is stored in transient callback context solely for token payment settlement and is never passed to `pool.swap` as the swap originator. [4](#0-3) 

**Two broken outcomes:**

1. **Allowlist bypass**: If the admin allowlists the router (the only way to let any user swap through it), every address — including those the admin explicitly never allowlisted — passes the extension check because the extension sees `sender = router` (allowlisted).

2. **Allowlisted users locked out**: If the admin allowlists individual user addresses (the intended design), those users cannot swap through the router at all, because the extension sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`.

No existing guard compensates for this: `SwapAllowlistExtension` has no mechanism to decode or verify the real originator from `extensionData`, and the pool interface provides no separate `realSender` field.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a permissioned institutional pool) can be fully bypassed by any unprivileged address routing through `MetricOmmSimpleRouter`. Real token flows occur — the attacker receives pool output tokens and the pool receives input tokens. The allowlist guard, the only access-control mechanism on the swap path, is rendered ineffective. This constitutes a broken core pool functionality and an admin-boundary break: an unprivileged path defeats a pool-admin-configured guard. [5](#0-4) 

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any user who discovers the allowlist can trivially route through the router. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. The pool admin has no on-chain mechanism to prevent this without removing the router from the allowlist entirely, which would lock out all router-mediated swaps. [6](#0-5) 

## Recommendation

Forward the real user identity through the swap path. Two options:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires extension cooperation and is opt-in per deployment.

2. **Add a `realSender` field to the pool's `swap` interface**: The pool accepts an explicit originator parameter (validated against `msg.sender` or a trusted-forwarder registry) and passes it to extensions. This is the cleanest fix but requires a core interface change.

Note: `DepositAllowlistExtension` does not share this bug because it checks `owner` (the position owner explicitly passed to `addLiquidity`), not `sender`. [7](#0-6) 

## Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that allowlisted users can swap through it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (never allowlisted) calls the router directly.
// The extension sees sender = router (allowlisted) → passes.
// Attacker receives pool output tokens.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds. Allowlist completely bypassed.
```

Call path: `attacker → router.exactInputSingle` → `pool.swap(msg.sender=router)` → `_beforeSwap(sender=router)` → `SwapAllowlistExtension.beforeSwap(sender=router)` → `allowedSwapper[pool][router] == true` → passes. The attacker's address is never checked. [8](#0-7)

### Citations

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
