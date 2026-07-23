Audit Report

## Title
`SwapAllowlistExtension` allowlist bypass via router intermediary — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of every `pool.swap()` call, not the actual user. A pool admin who allowlists the router address so that approved users can swap through it inadvertently grants swap access to every user, completely collapsing per-user access control.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender` to extensions.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`. [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument of `IMetricOmmExtensions.beforeSwap`. [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks only `sender`.**

`beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (mapping key) and `sender` is whoever called `pool.swap()`. [3](#0-2) 

**Step 3 — `MetricOmmSimpleRouter` is `msg.sender` of every `pool.swap()` call.**

`exactInputSingle` calls `pool.swap(params.recipient, ...)` directly; the router is `msg.sender`. [4](#0-3) 

The same applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165). [5](#0-4) 

**Step 4 — The allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.**

The extension has no visibility into the actual end user. `extensionData` is passed through but `SwapAllowlistExtension` ignores it entirely (the `bytes calldata` parameter is unnamed and unused). [3](#0-2) 

**Step 5 — Existing guards are insufficient.**

There is no mechanism in the pool, router, or extension to propagate the original caller's identity. The `allowAllSwappers` flag is a separate escape hatch and does not fix the per-user check. Once `allowedSwapper[pool][router] = true`, the check at line 37 passes for every caller of the router. [6](#0-5) 

## Impact Explanation
A pool protected by `SwapAllowlistExtension` is fully open to any user the moment the pool admin allowlists the router address. The allowlist invariant — "only approved addresses may swap" — is silently broken. Unauthorized users can execute swaps, extract value from LP positions, and interact with the pool in ways the admin explicitly intended to prevent. This constitutes broken core pool functionality causing direct loss of curation policy with potential LP principal loss in pools designed for restricted counterparties. [7](#0-6) 

## Likelihood Explanation
Allowlisting the router is the natural and expected action for any pool admin who wants their allowlisted users to be able to use the standard periphery interface. `MetricOmmSimpleRouter` is the primary user-facing swap interface. The admin has no alternative: if they do not allowlist the router, their approved users cannot swap through it; if they do, all users can. No documentation or code-level warning exists to prevent this. The condition is trivially reachable by any unprivileged user calling any of the four router swap functions. [8](#0-7) 

## Recommendation
The `sender` argument passed to `beforeSwap` must represent the economic actor (the end user), not the intermediary contract. Two complementary fixes:

1. **Router-level:** Have `MetricOmmSimpleRouter` ABI-encode `msg.sender` into `extensionData` as a prefix so extensions can recover the actual caller.
2. **Extension-level:** `SwapAllowlistExtension.beforeSwap` should decode the actual user from `extensionData` when `sender` is a known router, or the pool interface should expose a dedicated `originator` field that the pool sets to `tx.origin` or a router-supplied value, and have `SwapAllowlistExtension` check `originator` instead of `sender`. [3](#0-2) 

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedUser = 0xAAAA  (the only intended swapper)
  blockedUser = 0xBBBB  (should never be able to swap)

Admin actions:
  swapExtension.setAllowedToSwap(pool, allowedUser, true)
  swapExtension.setAllowedToSwap(pool, address(router), true)
    ↑ admin does this so allowedUser can swap via the router

Attack:
  blockedUser calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes — blockedUser receives tokens

Result:
  blockedUser successfully swaps in a pool they were never authorized to access.
  The allowlist is completely bypassed.
``` [1](#0-0) [6](#0-5) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
  }
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
