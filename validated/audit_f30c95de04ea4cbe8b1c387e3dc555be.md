Audit Report

## Title
Router-Mediated Swaps Check the Wrong Actor in `SwapAllowlistExtension`, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is always `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address instead of the actual user's address. If the pool admin allowlists the router (required for any router-mediated swap on an allowlisted pool), every non-allowlisted user can bypass the per-user allowlist by routing through the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` — so the pool sees `msg.sender = router`: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. This creates two mutually exclusive failure modes:

1. **Router not allowlisted** — allowlisted users cannot use the router at all (broken functionality).
2. **Router allowlisted** — every user, including those not on the per-user allowlist, can bypass the guard by routing through the router (security bypass).

The same wrong-actor problem applies to `exactInput` (intermediate hops use `address(this)` as payer, and the router is still `msg.sender` to each pool): [4](#0-3) 

The allowlist storage is keyed correctly by `(pool, swapper)` in the admin setters, but the hook reads the wrong identity at enforcement time: [5](#0-4) 

## Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise vetted addresses loses that guarantee the moment the router is allowlisted. Any address can call `MetricOmmSimpleRouter.exactInputSingle` and execute a swap against the restricted pool, receiving output tokens the pool admin intended to gate. This breaks the core allowlist invariant and constitutes a direct bypass of an admin-configured access control with fund-flow consequences — non-permitted parties receive pool output tokens. This matches the "Admin-boundary break" allowed impact: pool admin access control is bypassed by an unprivileged path.

## Likelihood Explanation

The router is a standard periphery contract that users are expected to use. A pool admin who configures a swap allowlist and also wants their permitted users to access the router must allowlist the router address — at which point the bypass is immediately available to all users. The trigger requires no privileged access, no special token behavior, and no unusual setup beyond the normal use of the router on an allowlisted pool.

## Recommendation

The extension must check the economically relevant actor — the end user — not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes it, verifying the pool is the caller (`msg.sender` in the extension) before trusting the payload.

2. **Maintain a trusted router registry**: When `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

The simplest correct fix is option 1: the router always appends the originating user to `extensionData`, and the extension decodes and checks that address instead of the raw `sender` argument.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle  →  pool.swap(msg.sender=router, ...)
    pool._beforeSwap(sender=router, ...)
    extension.beforeSwap(sender=router, ...)
      checks: allowedSwapper[pool][router] == true  ✓  (passes)

  Result: bob receives output tokens from the restricted pool.
  Direct call pool.swap() by bob would have been blocked:
      checks: allowedSwapper[pool][bob] == false  ✗  (reverts NotAllowedToSwap)
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
