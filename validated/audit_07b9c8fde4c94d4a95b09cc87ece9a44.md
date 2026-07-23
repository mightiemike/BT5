Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Caller, Enabling Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which equals `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's allowlist status rather than the actual user's. A pool admin who allowlists the router to let approved users swap through the standard periphery inadvertently opens the gate to every user, completely nullifying the access control mechanism.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the value forwarded from the pool's own `msg.sender`. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool:

```solidity
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

The original `msg.sender` is stored only in transient storage for the payment callback and is never surfaced to the pool or extension. The same identity mismatch exists in `exactInput` (intermediate hops use `address(this)` as sender) and `exactOutput` (recursive callback hops use `msg.sender` = the pool, not the original user).

The result is a structural dilemma: if the router is not allowlisted, allowlisted users cannot use the router (their swaps revert). If the router is allowlisted, every non-allowlisted user can bypass the restriction by routing through the router.

## Impact Explanation
This is an admin-boundary break. The swap allowlist is the sole on-chain mechanism for restricting who may trade against the pool. When a pool admin allowlists the router (the natural and expected configuration for allowing approved users to use standard periphery features like slippage protection and multi-hop), every unprivileged address gains the same swap access. Pools designed for restricted access — KYC-gated, partner-only, or compliance-constrained pools — accept swaps from any caller, violating the admin-boundary invariant. The access control guarantee the pool admin configured is completely nullified.

## Likelihood Explanation
The router is the canonical periphery swap path. Any pool admin who wants their allowlisted users to have normal UX (slippage protection, multi-hop, deadline enforcement) must allowlist the router. This is the expected and natural configuration, making the bypass reachable in any real deployment of the extension with router usage. The attack requires no special privileges — any unprivileged address can exploit it by calling the router.

## Recommendation
The extension must check the identity of the economic actor, not the intermediate contract. Two viable approaches:

1. **Pass original caller via `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks it. This requires a coordinated change to both the router and the extension, and the extension must verify the encoding comes from a trusted router.

2. **Document the limitation clearly**: If the extension is intentionally pool-direct-only, its NatSpec and deployment guide must state that allowlisting the router opens the gate to all users, and pool admins must not do so.

## Proof of Concept
```
Setup:
  pool deployed with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      ...
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...)         // msg.sender to pool = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓
          → no revert
      → swap executes for bob

Bob, who is not on the allowlist, successfully swaps because the extension
checked the router's allowlist entry rather than Bob's.
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
