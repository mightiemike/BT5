The key code is now fully traced. Let me verify the exact check in `SwapAllowlistExtension.beforeSwap` and the `sender` value it receives.

The full call chain is confirmed. Here is the determination:

---

### Title
Router Address Substitution Breaks `SwapAllowlistExtension` Per-User Guard — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the value passed by the pool — which is always `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the router is allowlisted for a pool, every EOA in existence can bypass the per-user swap allowlist by routing through it.

### Finding Description

**Step 1 — User calls the router:**
`MetricOmmSimpleRouter.exactInputSingle` is called by an unlisted EOA. [1](#0-0) 

The router stores `msg.sender` (the EOA) only in its internal callback context for payment purposes. It then calls `pool.swap(...)` directly — making the router itself the `msg.sender` of that call.

**Step 2 — Pool captures `msg.sender` as `sender`:**
`MetricOmmPool.swap` unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`. [2](#0-1) 

At this point `sender` = router address, not the originating EOA.

**Step 3 — Extension receives the router address as `sender`:**
`ExtensionCalling._beforeSwap` ABI-encodes `sender` (router) and dispatches it to `SwapAllowlistExtension.beforeSwap`. [3](#0-2) 

**Step 4 — Guard checks the router, not the EOA:**
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router. [4](#0-3) 

The extension is documented as "Gates `swap` by swapper address, per pool." [5](#0-4) 

The "swapper address" seen by the extension is the router, not the economic actor.

### Impact Explanation

A pool admin who wants to restrict swaps to a set of KYC'd or whitelisted EOAs will:
1. Allowlist those EOAs via `setAllowedToSwap`.
2. Also allowlist the router so those EOAs can use it.

Once the router is allowlisted, **any** EOA — including ones explicitly not in the allowlist — can call `router.exactInputSingle` and the guard passes because `allowedSwapper[pool][router] == true`. The per-user restriction is completely nullified. The pool's access control invariant is broken: non-permitted actors execute swaps and drain token0/token1 from a pool that was intended to be restricted.

Conversely, if the admin does **not** allowlist the router, then allowlisted EOAs cannot use the router at all, breaking core swap functionality for legitimate users.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap interface for the protocol. Any pool admin who deploys a `SwapAllowlistExtension` and also wants to support router-mediated swaps will hit this condition. The router allowlist path is the natural, expected configuration.

### Recommendation

Pass the originating user identity through the router to the pool, or have the extension resolve the true economic actor. Two concrete options:

1. **Router-side:** Have `exactInputSingle` (and all other swap entry points) encode `msg.sender` into `extensionData` in a standardized prefix slot, and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a known router.
2. **Extension-side:** Change `SwapAllowlistExtension` to also check `allowedSwapper[pool][tx.origin]` as a fallback, or introduce a separate `allowedRouter` mapping that explicitly does not grant blanket access to all users.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistedRouterBypassesPerUserAllowlist() public {
    // Setup: pool with SwapAllowlistExtension, only router is allowlisted
    extension.setAllowedToSwap(address(pool), address(router), true);
    // unlistedEOA is NOT in the allowlist

    // unlistedEOA routes through the router
    vm.prank(unlistedEOA);
    // Expect: should revert NotAllowedToSwap — but it SUCCEEDS
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        ...
        extensionData: ""
    }));
    // swap executes; unlistedEOA receives tokens from a restricted pool
}
```

The swap succeeds because `allowedSwapper[pool][router] == true`, and the extension never sees `unlistedEOA`.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-80)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-10)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
