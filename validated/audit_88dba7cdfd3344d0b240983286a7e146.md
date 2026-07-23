Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of original swapper, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates on the `sender` argument passed by the pool, which is `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original EOA. Any pool admin who allowlists the router to enable router-based swaps for their curated users simultaneously opens the pool to every unprivileged user who routes through the same router.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` at L230–231. When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` inside the pool is the router contract address. [1](#0-0) 

**Step 2 — The extension checks `sender` (the router) against the allowlist.**

`beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]`. The original EOA is never visible to the extension. [2](#0-1) 

**Step 3 — The router stores the original user only as the payment payer, never forwarding it to the pool.**

`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)` records the real swapper for the payment callback only. The subsequent `pool.swap()` call carries no user-identity argument, so the extension has no path to the original EOA. [3](#0-2) 

**Bypass chain:**
1. Pool admin creates a curated pool with `SwapAllowlistExtension` and allowlists Alice: `allowedSwapper[pool][alice] = true`.
2. Pool admin allowlists the router so Alice can use it: `allowedSwapper[pool][router] = true`.
3. Bob (not individually allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` — `msg.sender` in the pool is the router.
5. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
6. Bob's swap executes in the curated pool, bypassing the per-user gate.

The `allowedSwapper` mapping and `isAllowedToSwap` view function are designed for per-address granularity, but the check at L37 collapses all router users into a single identity. [4](#0-3) 

## Impact Explanation

Any unprivileged user can trade in a pool the admin intended to restrict to a named set of addresses. The allowlist provides zero protection once the router is allowlisted. Depending on the pool's purpose (preferential pricing, regulatory KYC gate, LP-only access), this results in unauthorized access to restricted liquidity, potential direct loss of LP assets or protocol fees, and a complete breakdown of the pool's access-control invariant. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break" criteria.

## Likelihood Explanation

The pool admin **must** allowlist the router to let their approved users trade through the standard periphery path — there is no other supported mechanism, as the router always calls `pool.swap()` as itself. A pool admin who sets up a curated pool and then enables router access (a routine, expected operational step) unknowingly opens the pool to everyone. The trigger is a valid, expected admin action, not a malicious one, making exploitation trivially repeatable by any user aware of the router address.

## Recommendation

The extension must gate on the economically responsible actor, not the direct caller of `pool.swap()`. Two viable fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap()` decode and check it when the caller is a known router.
2. **Extension-side**: Implement a payer-forwarding protocol via a shared transient-storage interface so the extension can read the original payer set by the router.

The simplest safe default: document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and remove the per-user allowlist path when the router is in use, or disallow allowlisting known router addresses entirely.

## Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists Alice for direct access
ext.setAllowedToSwap(pool, alice, true);
// Pool admin allowlists the router so Alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// Attack: Bob (not allowlisted) routes through the router
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:             pool,
    tokenIn:          token0,
    recipient:        bob,
    zeroForOne:       true,
    amountIn:         1000,
    amountOutMinimum: 0,
    priceLimitX64:    type(uint128).max,
    deadline:         block.timestamp + 1,
    extensionData:    ""
}));
// Bob's swap succeeds — allowlist bypassed
// allowedSwapper[pool][bob] == false, but extension saw allowedSwapper[pool][router] == true
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
