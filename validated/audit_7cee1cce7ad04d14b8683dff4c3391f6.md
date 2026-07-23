Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Per-Pool Swap Allowlist — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that caller is the router contract, not the end user. A pool admin who allowlists the router to support router-based swaps for approved users inadvertently grants every user who calls through the router implicit approval, rendering the per-user allowlist vacuous.

## Finding Description
The call chain is:

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. The router calls `IMetricOmmPoolActions(params.pool).swap(...)` directly — `msg.sender` inside the pool is the **router address**. [1](#0-0) 
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`. [2](#0-1) 
4. Inside `SwapAllowlistExtension.beforeSwap`, `msg.sender` = pool (verified by `onlyPool`) and `sender` = router. The guard evaluates `allowedSwapper[pool][router]`, never seeing the actual user. [3](#0-2) 

The router does not encode or forward the original caller's identity anywhere in the `extensionData` or any other parameter passed to the pool. [4](#0-3) 

The `allowedSwapper` mapping is keyed by `[pool][swapper]`, where `swapper` is the address the pool sees as `msg.sender` — the router — not the economically relevant actor. [5](#0-4) 

## Impact Explanation
This is an admin-boundary break: the pool admin's access-control invariant — that only explicitly approved addresses may swap — is broken for all router-mediated paths. Any non-allowlisted user can execute real swaps against a pool intended to be restricted (e.g., KYC-gated, institution-only, compliance-restricted) simply by calling through `MetricOmmSimpleRouter`. The corrupted value is the `allowedSwapper[pool][sender]` boolean decision: it resolves to `true` for the router when it should resolve to `false` for the unapproved end user. [6](#0-5) 

## Likelihood Explanation
Any pool that (a) configures `SwapAllowlistExtension` in its `beforeSwap` order and (b) allowlists the router so that approved users can use the standard periphery entry point is vulnerable. This is the expected operational pattern: the router is the primary user-facing entry point in the periphery layer. There is no in-protocol mechanism for a pool admin to allowlist the router for approved users while simultaneously blocking unapproved users who also call through the same router. The condition is reachable by any unprivileged trader with no special capability. [7](#0-6) 

## Recommendation
The `sender` forwarded to `beforeSwap` must represent the economically relevant actor, not the intermediary contract. Two viable approaches:

1. **Pass original caller in `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling the pool. The extension decodes and verifies it, with a trusted-router check inside the extension to prevent spoofing.
2. **Guard against router allowlisting**: Provide a wrapper extension that decodes a signed user identity from `extensionData` when the direct caller is a known router, and explicitly document that allowlisting the router address defeats the per-user gate.

The root fix is that the allowlist must gate the address that controls the economic decision (the end user), not the address that holds the ERC-20 approval or calls the pool.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension, router allowlisted
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// alice is NOT individually allowlisted
// assertFalse(swapExtension.isAllowedToSwap(address(pool), alice));

// Alice calls through the router — sender seen by extension = router (allowlisted)
vm.startPrank(alice);
token0.approve(address(router), type(uint256).max);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: alice,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        tokenIn: address(token0),
        deadline: block.timestamp,
        extensionData: ""
    })
);
vm.stopPrank();
// Swap succeeds — alice bypassed the per-user allowlist via the router
``` [3](#0-2) [8](#0-7)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-12)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
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
