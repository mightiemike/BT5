Audit Report

## Title
`SwapAllowlistExtension` Gates the Router's Identity Instead of the Original Swapper, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently opens the allowlist to every address on the network, because any caller can route through the same router.

## Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

So the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Once the router is allowlisted, the check passes for every caller regardless of who the actual end user is. The same structural problem applies to `exactInput` (intermediate hops use `address(this)` as payer/caller), `exactOutputSingle`, and `exactOutput`.

Existing guards are insufficient: there is no mechanism in the extension or the pool to recover the original `msg.sender` from the router call. The `extensionData` field is passed through but the extension does not inspect it.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC-approved counterparties or any curated set loses that restriction entirely the moment the router is allowlisted. Any unprivileged address can execute swaps against the pool's liquidity, directly exposing LP positions to uninvited and potentially adversarial order flow that the allowlist was designed to exclude. This constitutes a broken core pool functionality causing direct loss of LP assets — the allowlist invariant is the pool's primary access control for swap flow, and its complete bypass is a critical/high impact.

## Likelihood Explanation

The pool admin must allowlist the router for this to be exploitable, but this is a natural and expected operational step: without it, every allowlisted user is forced to call `pool.swap()` directly and loses access to multi-hop routing, ETH wrapping, permit flows, and slippage protection. The extension interface contains no warning that allowlisting the router collapses the allowlist to "allow all." A pool admin following the obvious deployment path will trigger the vulnerability.

## Recommendation

The extension must gate on the original end-user identity, not the immediate `pool.swap()` caller. Two sound approaches:

1. **Trusted-forwarder pattern**: The router encodes the original `msg.sender` in `extensionData`; the extension verifies the router's identity before trusting the forwarded address. Add a `trustedForwarder` mapping to the extension and, when `sender` is a trusted forwarder, decode and verify the real user from `extensionData`.
2. **Recipient-based gating**: Gate on `recipient` rather than `sender`. The recipient is set by the end user and is not substituted by the router.

## Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension attached to BEFORE_SWAP_ORDER.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC-approved
3. Pool admin calls setAllowedToSwap(pool, router, true)  // enable router for alice

Attack
──────
4. Bob (not KYC-approved) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      pool,
           recipient: bob,
           zeroForOne: true,
           amountIn:  X,
           ...
       })

5. Router calls pool.swap(bob, true, X, ...) → msg.sender in pool = router
6. Pool calls _beforeSwap(router, bob, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true → passes
8. Swap executes; Bob receives output tokens from the restricted pool.

Result: Bob bypassed the allowlist with zero privileged access.
```

Confirmed by:
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2) 
- [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-113)
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
