Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to enable standard periphery swap flows simultaneously grants every caller of the public router the ability to bypass the per-user allowlist entirely, rendering the access-control gate ineffective.

## Finding Description

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly, so `msg.sender` inside the pool is the router's address, not the originating EOA.

**Step 2 — Extension checks the router address, not the originating user.**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address: [2](#0-1) 

**Step 3 — Router is a public, permissionless contract.**

`MetricOmmSimpleRouter.exactInputSingle` has no access control; any EOA or contract can call it: [3](#0-2) 

**Step 4 — The natural admin configuration creates the bypass.**

A pool admin who wants to allow normal router-mediated swaps must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every swap routed through it, regardless of who the originating user is: [4](#0-3) 

There is no mechanism in the extension to decode the originating user from `extensionData` or any other field. The `recipient` argument (the second parameter, unnamed `address`) is ignored entirely in `beforeSwap`.

## Impact Explanation

The `SwapAllowlistExtension` is the production access-control gate for pools that restrict swapping to specific addresses (e.g., KYC-gated pools, private institutional pools, or pools restricted to specific market makers). Once the router is allowlisted — which is required for any pool that supports standard periphery swap flows — the allowlist provides zero protection. Any unprivileged user can swap by routing through `MetricOmmSimpleRouter`, consuming pool liquidity at oracle-derived prices without authorization. This constitutes a direct bypass of a configured security guard with fund-impacting consequences: unauthorized parties can drain liquidity from a restricted pool, representing a direct loss of LP principal from pools that are explicitly configured to prevent such access.

## Likelihood Explanation

The likelihood is high. `MetricOmmSimpleRouter` is the standard user-facing swap interface. Any pool admin who configures a `SwapAllowlistExtension` and also wants users to be able to swap via the router must allowlist the router address. The admin has no way to simultaneously allow router-mediated swaps and enforce per-user restrictions using the current extension design, making the misconfiguration essentially inevitable in any real deployment. The attack requires no special privileges — any EOA can call `exactInputSingle` on the public router.

## Recommendation

The extension must resolve the originating user identity, not the direct caller of `pool.swap()`. The cleanest fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it when `sender` is a known/trusted router address, similar to how Uniswap v4 hooks handle the `hookData` pattern. Alternatively, require that at least one of `sender` or `recipient` is allowlisted, preventing a non-allowlisted user from being the economic beneficiary of the swap.

## Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allow router for normal usage
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the only intended user
  - bob is NOT allowlisted

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ..., recipient: bob, ...})
  2. Router calls pool.swap(bob, zeroForOne, amount, ...)
     → msg.sender inside pool = router
  3. Pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob receives tokens from the restricted pool
  6. The allowlist check on bob's address is never performed

Foundry test outline:
  - Deploy pool with SwapAllowlistExtension
  - setAllowedToSwap(pool, router, true)
  - setAllowedToSwap(pool, alice, true)
  - vm.prank(bob); router.exactInputSingle(...)
  - Assert swap succeeds (bob receives output tokens)
  - Assert allowedSwapper[pool][bob] == false (bob was never authorized)
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
