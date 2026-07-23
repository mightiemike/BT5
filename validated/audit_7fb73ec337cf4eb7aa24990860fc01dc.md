Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of original caller, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to support standard periphery flows simultaneously opens the pool to every address that can call the router, defeating the allowlist entirely.

## Finding Description
**Root cause — three confirmed code facts:**

1. `MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

2. `ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

**Exploit path via `exactInputSingle`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original caller's identity: [4](#0-3) 

The pool therefore sees `msg.sender = address(router)`. The extension evaluates `allowedSwapper[pool][router]`. If the admin has allowlisted the router (the only way to let legitimate users use the standard periphery), this check passes for **every caller of the router**, regardless of whether they are individually allowlisted.

The same pattern applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165): [5](#0-4) 

**Existing guards are insufficient:** There is no secondary check on the original `tx.origin` or any `extensionData`-based identity forwarding. The `allowedSwapper` mapping is keyed only on the address the pool sees as its immediate caller.

## Impact Explanation
A pool admin deploying a curated pool with `SwapAllowlistExtension` (e.g., to restrict trading to KYC'd counterparties or institutional partners) faces an impossible choice: either block the router entirely (preventing allowlisted users from using the standard UX path) or allowlist the router (opening the pool to every unprivileged address). When the router is allowlisted, non-allowlisted users can execute swaps against LP positions at oracle-derived prices, draining LP principal through unauthorized swap execution. This is a direct loss of LP funds on a pool whose entire value proposition is access control — matching the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, documented periphery entry point. Any pool admin who wants allowlisted users to use the router must allowlist the router address — this is the expected operational pattern, not an edge case. The bypass is reachable by any unprivileged user on any curated pool whose admin has followed the natural integration path, with no special privileges or setup required beyond calling a public router function.

## Recommendation
The extension must recover the original user's identity rather than relying on the `sender` argument. Two viable approaches:

1. **Forward original caller through `extensionData`:** Modify `MetricOmmSimpleRouter` to encode `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it against a trusted-router registry, then gate on that address instead of `sender`.

2. **Router registry with fallback:** If `sender` is a known trusted router, require the extension payload to carry the verified original caller address and gate on that address. If `sender` is not a known router, gate on `sender` directly (preserving current behavior for direct swaps).

The simplest correct fix is to never allowlist the router as a swapper and instead require the router to forward the original caller's identity in a verifiable, extension-readable way.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension, only alice is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin allowlists the router so alice can use standard periphery
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) swaps through the router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    tokenIn: token0,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Pool sees msg.sender = router → _beforeSwap(sender=router, ...)
// Extension checks allowedSwapper[pool][router] → true → passes
// Bob's swap executes against LP funds despite not being allowlisted
```

The `NotAllowedToSwap` revert at `SwapAllowlistExtension.sol` line 38 is never reached because `allowedSwapper[pool][router]` is `true`, and the extension has no way to distinguish bob from alice when both arrive via the router. [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
