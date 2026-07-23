Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Any User to Bypass the Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is the immediate caller, so the extension checks whether the **router** is allowlisted rather than the **actual user**. If the router is allowlisted — the only way any router-mediated swap can succeed — every unprivileged user bypasses the per-user allowlist by routing through the public router.

## Finding Description

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` ABI-encodes that same `sender` and forwards it to every configured extension: [2](#0-1) 

When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` to the pool, so `sender = router address` reaches the extension: [3](#0-2) 

**Step 2 — The extension checks the router's address, not the user's address.**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender = pool` and `sender = router`. The check resolves to `allowedSwapper[pool][router]` regardless of which end-user initiated the call: [4](#0-3) 

**Step 3 — The pool admin faces an impossible choice.**

| Router allowlisted? | Effect |
|---|---|
| No | Legitimate allowlisted users cannot use the router — broken core functionality. |
| Yes | `allowedSwapper[pool][router] == true` for every call through the router, so **any** user bypasses the per-user allowlist. |

There is no configuration that simultaneously (a) lets allowlisted users use the router and (b) blocks non-allowlisted users from using the router, because the extension has no visibility into the original user's identity.

## Impact Explanation

`SwapAllowlistExtension` is the sole on-chain enforcement mechanism for curated pools restricting trading to specific addresses (e.g., KYC-gated, institutional-only, or compliance-restricted pools). Once the router is allowlisted — a necessary operational step for any router-mediated swap — the allowlist is completely bypassed. Any unprivileged user can trade on the restricted pool under pricing or liquidity conditions intended only for vetted counterparties. This constitutes broken core pool functionality and a direct loss of the pool's curation guarantee.

## Likelihood Explanation

High. `MetricOmmSimpleRouter` is a public, permissionless contract. No special privileges, tokens, or setup are required beyond a standard ERC-20 approval. The bypass is a single router call. The only precondition — the router being allowlisted — is a necessary operational condition for the pool to be usable through the standard periphery at all.

## Recommendation

The extension must check the **original user's address**, not the immediate pool caller. Two viable approaches:

1. **Router-forwarded identity**: The router encodes `msg.sender` into `extensionData` before calling the pool, and the extension decodes and verifies it (requires a trusted router check inside the extension).
2. **Direct-call-only policy**: Document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated swaps; add a revert in the extension if `sender` matches any known router address.

A minimal diff for approach 1:

```diff
- function beforeSwap(address sender, ...) external view override returns (bytes4) {
-     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+ function beforeSwap(address sender, ..., bytes calldata extensionData) external view override returns (bytes4) {
+     address effectiveSender = extensionData.length == 20
+         ? address(bytes20(extensionData))
+         : sender;
+     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][effectiveSender]) {
          revert IMetricOmmPoolActions.NotAllowedToSwap();
      }
```

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)       // alice is the intended user
  pool admin: setAllowedToSwap(pool, router, true)      // required for router-mediated swaps

Attack (charlie, not allowlisted):
  charlie calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...) with msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → checks allowedSwapper[pool][router] → true  ✓
    → swap executes

Result:
  charlie, who is not allowlisted, successfully swaps on the curated pool.
  The allowlist is completely bypassed.
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
