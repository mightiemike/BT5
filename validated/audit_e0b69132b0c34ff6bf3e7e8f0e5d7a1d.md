Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Per-User Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract. If the pool admin allowlists the router to enable router-mediated swaps for their allowlisted users, every non-allowlisted user can bypass the per-user allowlist by routing through the public router.

## Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. In `MetricOmmSimpleRouter.exactInputSingle`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The router calls `pool.swap()` directly, making itself the `msg.sender` the pool sees. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][bob]`. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. When the admin allowlists the router (the natural action to enable router-mediated swaps for their allowlisted users), `allowedSwapper[pool][router]` is `true`, and any unprivileged user can call `MetricOmmSimpleRouter` to swap on the curated pool.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to specific addresses (e.g., KYC-verified users, institutional counterparties). To also support router-mediated swaps for those allowlisted users, the admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and any unprivileged user can call `MetricOmmSimpleRouter` to swap on the curated pool. The per-user allowlist is completely nullified. This is a direct admin-boundary break: an unprivileged path bypasses a pool access control check that the pool admin explicitly configured. Unauthorized users gain full swap access to a pool whose design intent is to restrict trading to vetted participants.

**Impact: Medium** — direct policy bypass on curated pools; unauthorized users trade on restricted pools.

## Likelihood Explanation

The bypass requires two conditions: (1) the pool admin allowlists the router, and (2) a non-allowlisted user uses the router. Condition (1) is the natural and expected action for any admin who wants their allowlisted users to benefit from the router's slippage protection and multi-hop routing. Condition (2) is trivially achievable by any user. No existing guard prevents this: the extension only checks the direct caller of `pool.swap()`, not the end user.

**Likelihood: Medium** — requires a plausible and expected admin configuration.

## Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor — the end user — not the intermediary router. The cleanest fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it when the caller is a known router. Alternatively, document that the router must never be allowlisted and that allowlisted users must call the pool directly, though this degrades UX for curated pools.

## Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists alice (KYC'd user) and the router (for router support)
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// alice adds liquidity (she is allowlisted for deposits too)
vm.prank(alice);
pool.addLiquidity(alice, 0, deltas, "", "");

// bob is NOT allowlisted
assertFalse(swapExtension.isAllowedToSwap(address(pool), bob));

// bob bypasses the allowlist by routing through the public router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: bob,
        tokenIn: token1,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// swap succeeds — bob traded on a pool he should not have access to
// because allowedSwapper[pool][router] == true
```