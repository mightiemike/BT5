Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's address rather than the originating user's address. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user of the public router, completely defeating the allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user's address: [4](#0-3) 

At that point `msg.sender` inside `pool.swap` is the router address, so `sender` forwarded to the extension is the router, not the originating user. The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router — the only way to permit router-mediated swaps at all — every user who calls through the router passes the check unconditionally. There is no existing guard that recovers the originating caller's identity; `extensionData` is passed through from the user and is not authenticated.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or compliance-gated participants) is fully bypassed by any unprivileged user who routes through the public `MetricOmmSimpleRouter`. The attacker requires no special role or token approval beyond what a normal swap requires. Every LP in the pool is exposed to swap flow from actors the pool admin explicitly intended to exclude, which can cause direct LP value loss through toxic or adversarial flow that the allowlist was designed to prevent. This constitutes a broken core pool functionality (the allowlist guard) causing potential loss of funds to LPs, and an admin-boundary break where the pool admin's configured access control is bypassed by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who deploys a swap-allowlisted pool and wants legitimate allowlisted users to trade through the standard router must allowlist the router address. The moment they do, the guard is open to everyone. The admin has no in-protocol mechanism to allowlist the router for some users but not others, because the extension receives only the router's address, not the originating caller. The misconfiguration is a near-inevitable consequence of normal operational setup, requiring no special attacker capability beyond calling the public router.

## Recommendation
The extension must verify the identity of the originating user, not the intermediary. Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: Require the router to ABI-encode `msg.sender` into `extensionData` for allowlisted pools, and update `SwapAllowlistExtension.beforeSwap` to decode and check that value when `sender` is a known router. This requires coordination between the router and extension, and the extension must maintain a registry of trusted routers whose `extensionData` identity claims are accepted.

2. **Check `recipient` as a proxy**: For single-hop swaps where the user is also the recipient, checking `recipient` instead of `sender` would correctly identify the economic actor. This does not generalise to multi-hop paths where the router is the intermediate recipient.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  pool admin calls setAllowedToSwap(pool, router, true)  // needed for router-mediated swaps

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})
      → router calls pool.swap(recipient, ...) with msg.sender = router
      → pool calls _beforeSwap(router, ...)
      → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
      → swap executes successfully for bob

Result:
  bob, who is not on the allowlist, completes a swap on a restricted pool.
  The allowlist provides zero protection for any user who routes through the router.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
