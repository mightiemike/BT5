Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the end-user, making the allowlist either fully broken or fully bypassable for router-routed swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the router contract — not the originating user. The check `allowedSwapper[msg.sender][sender]` therefore evaluates the router's allowlist status, not the trader's. This produces two mutually exclusive, fund-impacting failure modes: either the router is not allowlisted (breaking all router-based swaps for every user, including individually allowlisted ones), or the router is allowlisted (allowing every user to bypass the allowlist through the router).

## Finding Description
**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ..., params.extensionData)` — the pool sees `msg.sender = router`. The user's identity is never encoded into `extensionData` or any other argument.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, passing the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and forwards that same `sender` (router) to the extension via `_callExtensionsInOrder`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`.

The end user's address is never consulted. The router passes `params.extensionData` verbatim from the caller, but the extension ignores `extensionData` entirely — it only reads `sender`.

**Root cause in `SwapAllowlistExtension.beforeSwap`:** [1](#0-0) 

**`MetricOmmPool.swap` passes `msg.sender` (the router) as `sender`:** [2](#0-1) 

**`ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension:** [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` with no user identity forwarding:** [4](#0-3) 

Existing guards are insufficient: `BaseMetricExtension` only validates that `msg.sender` is a registered pool; it does not validate the identity of the economic actor. The `allowAllSwappers` escape hatch is a pool-level toggle, not a per-user bypass, and does not resolve the structural mismatch.

## Impact Explanation
Two mutually exclusive fund-impacting failure modes:

**Mode A — Router not allowlisted (expected operator configuration):** A pool admin allowlists specific KYC'd or whitelisted user addresses. The router is not on the list. Every swap attempt through `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` reverts with `NotAllowedToSwap`, even for individually allowlisted users. The pool's primary public swap interface is completely broken. LPs cannot receive fees; traders cannot execute; the pool is economically insolvent relative to its intended operation. This is broken core pool functionality causing loss of funds (LP fee loss).

**Mode B — Router allowlisted to unblock router-based swaps:** The admin adds the router to the allowlist. `allowedSwapper[pool][router] = true`, so `beforeSwap` passes for every call arriving through the router — regardless of who the end user is. Any address, including those the admin explicitly excluded, can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle`. The curation policy is completely defeated, constituting an admin-boundary break by an unprivileged path.

## Likelihood Explanation
`SwapAllowlistExtension` is a production periphery contract designated for curated pools. Any pool deploying with this extension as its `beforeSwap` hook and expecting users to interact through `MetricOmmSimpleRouter` (the standard public router) will immediately encounter one of the two failure modes above under normal usage. No special attacker setup is required — the failure is structural and triggered by the standard swap path. The router is the primary intended interface for end users.

## Recommendation
The extension must identify the economic actor, not the immediate caller. The cleanest fix is to have the router encode `msg.sender` into `extensionData` before calling `pool.swap` (e.g., `abi.encode(msg.sender)`), and have `SwapAllowlistExtension.beforeSwap` decode and check that address against the allowlist when `extensionData` is non-empty, while still verifying `msg.sender` (the pool) is a registered pool. Since the router is factory-validated, it is already a trusted intermediary. Alternatively, a separate `sender` forwarding field could be added to the swap interface, but that requires core changes.

## Proof of Concept
```solidity
// Setup:
// 1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
// 2. Pool admin allowlists alice but NOT the router.
// 3. Alice calls MetricOmmSimpleRouter.exactInputSingle(...).
// Expected: alice's swap succeeds (she is allowlisted).
// Actual:   alice's swap reverts NotAllowedToSwap because
//           allowedSwapper[pool][router] == false.

// Conversely:
// 1. Pool admin allowlists the router to unblock router-based swaps.
// 2. Bob (not individually allowlisted) calls router.exactInputSingle.
// Expected: bob's swap reverts (he is not allowlisted).
// Actual:   bob's swap succeeds because allowedSwapper[pool][router] == true.
//           Bob received output tokens despite not being on the allowlist.
```

The root cause is confirmed at `SwapAllowlistExtension.beforeSwap` line 37, which evaluates `allowedSwapper[msg.sender][sender]` where `sender` is always the router when the standard periphery path is used. [5](#0-4)

### Citations

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
