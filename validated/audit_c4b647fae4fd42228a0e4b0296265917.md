Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` from `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the original user. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every caller of the router, defeating the per-user allowlist entirely.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231, passing the direct caller as `sender`. [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool. [3](#0-2) 

The original user's address (`msg.sender` at the router level) is stored only in transient callback context for payment purposes and is never forwarded to the extension. [4](#0-3) 

The same collapse occurs for every hop in `exactInput`, where intermediate hops are also called by the router itself. [5](#0-4) 

The extension has no mechanism to distinguish individual users behind the same router address. Once `allowedSwapper[pool][router] == true`, the check at line 37 passes for every caller of the router unconditionally. [2](#0-1) 

## Impact Explanation
Any non-allowlisted user can trade on a curated pool by routing through `MetricOmmSimpleRouter` whenever the router address is in the pool's allowlist. The pool admin's intent — restricting trading to a specific set of addresses — is silently defeated. This constitutes an admin-boundary break: an unprivileged path bypasses the pool admin's access control, enabling unauthorized price impact, fee extraction, or LP value leakage by actors the admin explicitly excluded.

## Likelihood Explanation
The bypass requires the router to be allowlisted. A pool admin who wants their curated users to access the router has no other option: the extension provides no mechanism to allowlist "user X via the router." Allowlisting the router is therefore a natural and expected administrative action, making the precondition reachable in any production deployment combining `SwapAllowlistExtension` with `MetricOmmSimpleRouter`. The attack requires no special privileges — any EOA can call `exactInputSingle` on the router.

## Recommendation
The `sender` argument passed to `beforeSwap` must represent the economically responsible actor, not the intermediary contract. Two viable fixes:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks that address only when `sender` is a known trusted router. The pool admin must separately allowlist the router as a trusted forwarder.
2. **Separate trusted-router registry**: Distinguish between "this router is a trusted forwarder" and "this user is allowed to swap." The extension checks the decoded user from extension data only when `sender` is a known trusted router, and falls back to checking `sender` directly otherwise.

The extension must never treat the router address as the identity to gate.

## Proof of Concept
```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap hook

// Admin allowlists the router so their users can use it
vm.prank(poolAdmin);
ext.setAllowedToSwap(address(pool), address(router), true);

// Non-allowlisted attacker routes through the router
vm.prank(attacker); // attacker NOT in allowedSwapper[pool]
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    recipient: attacker,
    amountIn: 1e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds: extension checked allowedSwapper[pool][router] == true
// attacker traded on a pool they were never allowlisted for
```

The `beforeSwap` check at line 37 of `SwapAllowlistExtension.sol` evaluates `allowedSwapper[pool][router]` (true) and never inspects the original `attacker` address, completing the bypass. [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
