Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` always sets to `msg.sender` — the direct pool caller. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router (to let legitimate users reach the pool via the standard periphery) simultaneously grants every non-allowlisted user the ability to bypass the gate by calling through the same public router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← always the direct pool caller
  recipient,
  ...
);
```

`_beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder`.

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is in the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,   // recipient = end user
    ...
    params.extensionData
  );
```

The same pattern holds for `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165). In every case, the router is the direct caller of the pool, so `sender` delivered to the extension is always the router address, never the end user.

The pool admin faces an inescapable dilemma:
- **Router not allowlisted:** Every router-mediated swap reverts, breaking the standard periphery path even for allowlisted users.
- **Router allowlisted:** Every user — allowlisted or not — can bypass the gate by routing through the public router contract.

No existing guard in `SwapAllowlistExtension` or `MetricOmmPool` checks the economic actor (the end user) rather than the direct caller.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, protocol-internal actors, or whitelisted market makers) loses that protection entirely for any user who calls through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps of arbitrary size, draining pool liquidity or extracting value at oracle-anchored prices that the pool admin intended to reserve for specific parties. This constitutes a direct loss of LP assets and a curation failure on the pool's core access-control invariant — a High severity impact under Sherlock thresholds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the standard periphery will encounter this issue immediately upon the admin allowlisting the router. No special conditions, privileged access, flash loans, or unusual token behavior are required — a single call to `exactInputSingle` from any EOA suffices. The attack is repeatable and permissionless.

## Recommendation
Pass the economically relevant actor — the end user — rather than the direct pool caller. Two approaches:

1. **Router-side (preferred):** Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and verify it against a trusted-router registry. The extension reads the real swapper from `extensionData` when the direct `sender` is a registered router, falling back to `sender` for direct pool calls.
2. **Extension-side:** Change `SwapAllowlistExtension.beforeSwap` to check `recipient` instead of `sender` when `sender` is a known router, noting that `recipient` and the economic actor may differ in multi-hop paths.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  allowedSwapper[pool][alice] = true   // alice is the intended gated user
  allowedSwapper[pool][router] = true  // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(bob, ...)          // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes for bob

Result: bob swaps successfully despite not being in the allowlist.
        If admin does NOT add router, alice also cannot use the router.
```

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2)

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
