Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Per-User Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool, not the end user. If a pool admin allowlists the router to support router-mediated swaps for their vetted users, every non-allowlisted user can bypass the per-user allowlist by routing through the public router.

## Finding Description
In `MetricOmmPool.swap`, the pool passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that `sender` unchanged to the extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, the router is `msg.sender` at the pool:

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

The router itself becomes `sender` in the extension check. A pool admin who wants allowlisted users to benefit from the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and any unprivileged user can call the router to swap on the curated pool — the per-user allowlist is completely nullified. Existing guards (`allowAllSwappers` and `allowedSwapper`) are both checked against the router address, not the end user, so neither guard catches the bypass.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to specific addresses (e.g., KYC-verified users, institutional counterparties). Allowlisting the router — the natural action to enable router-mediated swaps for those allowlisted users — causes `allowedSwapper[pool][router]` to be `true`. Any unprivileged user can then call `MetricOmmSimpleRouter` to swap on the curated pool. The per-user allowlist is completely nullified, and unauthorized users gain full swap access to a pool whose design intent is to restrict trading to vetted participants. This is a direct admin-boundary break: an unprivileged path bypasses the pool admin's access-control policy.

## Likelihood Explanation
Two conditions are required: (1) the pool admin allowlists the router, and (2) a non-allowlisted user uses the router. Condition (1) is the natural and expected action for any admin who wants their allowlisted users to benefit from the router's slippage protection and multi-hop routing. Condition (2) is trivially achievable by any user. No special privileges, flash loans, or unusual token behavior are required.

## Recommendation
The extension must gate the economically relevant actor — the end user — not the intermediary router. The cleanest fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it when the caller is a known, trusted router. Alternatively, document explicitly that the router must never be allowlisted and that allowlisted users must call the pool directly, though this degrades UX for curated pools.

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

The router calls `pool.swap()` with `msg.sender = router`. The pool passes `router` as `sender` to `_beforeSwap`. The extension checks `allowedSwapper[pool][router]` which is `true`, so bob's swap is accepted despite bob not being allowlisted.