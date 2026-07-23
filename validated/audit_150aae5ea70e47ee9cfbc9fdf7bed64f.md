Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Per-User Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router address, not the originating user. A pool admin who allowlists the router (to enable router-based trading) simultaneously grants every user — including explicitly non-allowlisted ones — the ability to bypass the per-user restriction by routing through the official periphery.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to each configured extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then checks only `sender` (the first parameter) against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router `msg.sender` to the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,   // ← actual user set as recipient
    params.zeroForOne,
    ...
);
```

**Exploit path:**
1. Pool admin configures `SwapAllowlistExtension`, allowlists `userA` and the router (to support official UI), but does NOT allowlist `userB`.
2. `userB` calls `router.exactInputSingle(...)`.
3. Router calls `pool.swap(userB_address, ...)` → `msg.sender` in pool = router.
4. Extension checks `allowedSwapper[pool][router]` = `true` → passes.
5. `userB`'s swap executes successfully, bypassing the per-user restriction.

The pool admin cannot simultaneously allow router-based trading and enforce per-user allowlist restrictions under the current design. These two goals are mutually exclusive.

## Impact Explanation
The configured swap allowlist — a core pool access-control mechanism — is rendered completely ineffective for any pool that supports the official router. Any user excluded from the allowlist can bypass the restriction by routing through `MetricOmmSimpleRouter`. This directly matches the contest's "Allowlist path" Smart Audit Pivot: *"deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router."* The impact is broken core pool functionality; the pool admin's intent to restrict trading to specific addresses is nullified. **Severity: Medium.**

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router, which is the natural and expected configuration for any curated pool that wants to support the official periphery UI. Pool admins who configure both per-user restrictions and router support will unknowingly expose the bypass. The `SwapAllowlistExtension` NatSpec ("Gates `swap` by swapper address") does not warn that router intermediaries collapse all users into a single identity. The condition is easily and repeatedly triggerable by any non-allowlisted user.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should check the actual economic actor, not the immediate caller. The cleanest fix is to require the router to embed the originating user address in `extensionData`, and have the extension decode and check it. Alternatively, check `recipient` instead of `sender` as a partial fix (the router sets `recipient` to the actual user in `exactInputSingle`/`exactOutputSingle`). A third option is to explicitly document that pools using `SwapAllowlistExtension` must not allowlist any intermediary contract, including the official router.

## Proof of Concept
```solidity
// Pool deployed with SwapAllowlistExtension configured.
// Pool admin allowlists userA and the router, but NOT userB.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), userA, true);
// userB is NOT allowlisted

// Direct call by userB → correctly reverts
vm.prank(userB);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(userB, false, int128(1000), type(uint128).max, "", "");

// Router call by userB → bypasses allowlist, succeeds
vm.prank(userB);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    recipient: userB,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token1,
    extensionData: ""
}));
// swap executes successfully — userB bypassed the allowlist
```