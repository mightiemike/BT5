Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any caller to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` validates the `sender` argument, which the pool always sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end-user. A pool admin who allowlists the router so that their permissioned users can access the pool via the periphery inadvertently grants every unpermissioned address the same access, completely defeating the allowlist guard.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first positional argument forwarded by the pool. [1](#0-0) 

`MetricOmmPool.swap` always passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    ...
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router contract the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        ...
    );
``` [3](#0-2) 

The same pattern applies to `exactOutputSingle` and `exactInput`/`exactOutput` multi-hop variants. [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The `extensionData` bytes forwarded by the router are passed through but completely ignored by `SwapAllowlistExtension`, so there is no in-band escape hatch. [1](#0-0) 

The pool admin faces an inescapable dilemma:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all |
| Router **allowlisted** | **Every** address can bypass the allowlist via the router |

## Impact Explanation

A pool admin who deploys a restricted pool (e.g., for institutional counterparties, KYC-gated users, or whitelisted market makers) and allowlists the router to give those users periphery access inadvertently opens the pool to all callers. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's LP liquidity without restriction. LP principal is directly exposed to adverse-selection or toxic flow that the allowlist was designed to prevent — constituting a direct loss of LP assets above Sherlock thresholds. [5](#0-4) 

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard user-facing swap entry point providing slippage protection, multi-hop routing, and deadline enforcement. A pool admin who wants allowlisted users to enjoy these features must allowlist the router — this is the natural and expected operational step, making the misconfiguration highly probable in any real deployment of a restricted pool. The attack requires no special capability: any EOA or contract can call `exactInputSingle` with the target pool address. [3](#0-2) 

## Recommendation

The extension must verify the **originating** user, not the immediate pool caller. Three viable approaches:

1. **Router-forwarded identity via `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with a trusted-forwarder pattern or signature to prevent spoofing by arbitrary callers).
2. **Check `recipient` instead of `sender`**: If the pool's design intent is to gate who *receives* output tokens, check the `recipient` argument instead. This is not equivalent to gating the payer but may match some use-cases.
3. **Document that the router must never be allowlisted**: Treat the router as an untrusted intermediary and require allowlisted users to call `pool.swap()` directly. This is the safest short-term fix but breaks periphery usability. [1](#0-0) 

## Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is trusted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // so Alice can use the router

Attack:
  - Charlie (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(params.recipient, ...)
      → pool passes msg.sender = router as `sender` to _beforeSwap
      → extension.beforeSwap(router, ...) is called
  - Extension checks allowedSwapper[pool][router] → true
  - Charlie's swap executes; allowlist is bypassed.

Result:
  - Charlie trades against LP liquidity intended to be restricted.
  - LP principal is exposed to unrestricted toxic flow.
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` configured.
2. Call `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. From a `charlie` address (not allowlisted), call `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
4. Assert the swap succeeds (no `NotAllowedToSwap` revert), confirming the bypass. [6](#0-5)

### Citations

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
