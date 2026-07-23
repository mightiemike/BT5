Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension checks the router's address rather than the actual user's address. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently opens the pool to every user, completely defeating the per-user gate.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the contract calling the extension) and `sender` is the address the pool forwarded. [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-231
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then forwards this `sender` value unchanged into the extension call: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The same pattern applies to `exactOutputSingle` and `exactInput`. [5](#0-4) 

The extension therefore evaluates `allowedSwapper[pool][router_address]`. If the admin allowlists the router (the only way to let approved users use the router), the check passes for **any** caller routing through the router, not just approved users. There is no mechanism in the extension or the pool to recover the original EOA.

## Impact Explanation
The allowlist extension's core invariant — that only approved addresses may swap — is completely broken for router-mediated paths. Any unapproved user can bypass the gate by calling `MetricOmmSimpleRouter` whenever the router is allowlisted. This enables unauthorized users to swap on pools the admin intended to restrict (e.g., KYC-gated, risk-managed, or counterparty-restricted pools), constituting broken core pool functionality with direct fund-impacting consequences for LPs and allowlisted counterparties.

## Likelihood Explanation
Medium. A pool admin deploying a restricted pool with `SwapAllowlistExtension` will naturally want approved users to use the standard router. Allowlisting the router is the only available mechanism to enable this, and it is a straightforward, expected admin action. The admin has no indication from the extension's interface or documentation that doing so opens the pool to all users. The router is a public, permissionless contract callable by any address.

## Recommendation
The extension must check the identity of the actual initiating EOA, not the intermediary contract. Two viable approaches:

1. **Router-forwarded identity in `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks that address, trusting only a factory-registered router address as the source.
2. **Pool-level originator tracking**: The pool stores the original caller or a router-forwarded address in transient storage before calling extensions, and the extension reads it from there.

Until fixed, pool admins must be explicitly warned that allowlisting the router is equivalent to setting `allowAllSwappers = true`.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only user A is approved.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so user A can use it.
4. User B (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient, ...)` — pool passes `sender = router` to the extension.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. User B's swap executes successfully on the restricted pool.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
