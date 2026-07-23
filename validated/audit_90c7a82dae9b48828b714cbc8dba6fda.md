Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the real swapper, allowing any user to bypass the curated-pool swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives the `sender` argument forwarded from `MetricOmmPool.swap`, which is `msg.sender` of the pool call — the router contract, not the end user. A pool admin who allowlists the router to enable standard periphery swaps inadvertently grants every user on the network the ability to bypass the allowlist by routing through `MetricOmmSimpleRouter`.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` uses that value as the swapper identity, checking `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the received argument: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

At that call site, `msg.sender` of the pool is the router contract. The actual user's address is stored only in transient callback context via `_setNextCallbackContext` for payment settlement: [4](#0-3) 

It is never forwarded to extensions. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`. This creates an irreconcilable dilemma: not allowlisting the router makes the standard periphery path unusable on the pool; allowlisting the router makes the check trivially true for every user who routes through it.

## Impact Explanation
Any unprivileged user can bypass the swap allowlist on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). If the allowlist was deployed to restrict trading to specific counterparties on an oracle-anchored pool, the bypass lets arbitrary actors trade against LP positions at oracle prices, directly extracting value from LPs in ways the pool admin explicitly intended to prevent. This is a broken admin-boundary / direct loss-of-LP-principal path reachable by any unprivileged user. [5](#0-4) 

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, documented swap entry point for end users. Any pool admin who wants allowlisted users to trade through the normal UX will naturally allowlist the router. The bypass requires no special knowledge or privilege: any user who observes the pool has a swap allowlist simply calls the router instead of the pool directly. It is a single public transaction with no preconditions beyond token approval. [6](#0-5) 

## Recommendation
The extension must check the economically relevant actor, not the immediate caller of the pool. Two viable approaches:

1. **Pass the real user through `extensionData`:** Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it, enforcing that the caller is a factory-registered trusted router before trusting the encoded value.

2. **Trusted-router registry:** Maintain a factory-level registry of trusted routers; when `sender` is a trusted router, decode the real user from `extensionData`; otherwise use `sender` directly.

Either approach must be applied symmetrically to `DepositAllowlistExtension` if the same `sender`-forwarding pattern is used for liquidity adder paths. [7](#0-6) 

## Proof of Concept
```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap extension
  admin calls extension.setAllowedToSwap(pool, router, true)
    // admin intends to allow router-mediated swaps for allowlisted users
  alice = allowlisted EOA
  bob   = non-allowlisted EOA

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    // router calls pool.swap(...) — msg.sender of pool = router
    // pool calls extension.beforeSwap(sender=router, ...)
    // extension checks allowedSwapper[pool][router] == true  ✓
    // swap executes — bob bypassed the allowlist

Verification:
  bob calls pool.swap(...) directly
    // pool calls extension.beforeSwap(sender=bob, ...)
    // extension checks allowedSwapper[pool][bob] == false  → NotAllowedToSwap ✓
```

The direct call correctly blocks `bob`, but the router path succeeds because the extension sees the router's address, not `bob`'s. The allowlist invariant is broken for every user who routes through the public periphery. [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-29)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }
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
