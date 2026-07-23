Audit Report

## Title
`SwapAllowlistExtension` allowlist check gates on the router address instead of the end user, allowing any address to bypass per-user swap restrictions — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, the router is `msg.sender` of the pool call, not the end user. Because the router must be allowlisted for any router-mediated swap to succeed, every non-allowlisted user can bypass the per-user gate by calling the router instead of the pool directly.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` (the immediate caller) as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — i.e., whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of the pool call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    ...
  );
```

The same pattern holds for `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165). For a pool with `SwapAllowlistExtension` to be usable through the router at all, the pool admin **must** add the router to `allowedSwapper`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every user who routes through it, regardless of whether that user is individually allowlisted. The per-user gate is completely bypassed.

## Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, protocol-owned accounts, or whitelisted market makers). Any non-allowlisted address can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the restricted pool. The router is the `sender` the extension sees, so the allowlist check passes. The unauthorized user executes a live swap, receiving output tokens from the pool and paying input tokens — a direct, fund-impacting bypass of the intended access control. This constitutes broken core pool functionality (access control) causing unauthorized fund movement.

## Likelihood Explanation

The bypass requires no special privileges, no malicious setup, and no non-standard tokens. Any user who knows the pool address can call the public router. The router must be allowlisted for the pool to be usable through the standard periphery at all, so the precondition is met in every realistic deployment. The attack is a single transaction with no additional requirements.

## Recommendation

The allowlist must gate on the **end user**, not the immediate pool caller. Two sound approaches:

1. **Gate on `recipient` instead of `sender`**: Change the check to `allowedSwapper[pool][recipient]` (the second argument to `beforeSwap`). This correctly identifies who benefits from the swap regardless of routing path.

2. **Pass the original user through `extensionData`**: Require callers to embed the true end-user address in `extensionData` and verify it in the extension. The router would need to forward `msg.sender` in `extensionData`.

```solidity
// Fixed option: gate on recipient (second argument)
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — required for any router-mediated swap to work.
3. Pool admin calls `setAllowedToSwap(pool, alice, false)` — Alice is not individually allowlisted.
4. Alice calls `router.exactInputSingle({pool: restrictedPool, recipient: alice, ...})`.
5. Router calls `pool.swap(recipient=alice, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, recipient=alice, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Alice receives output tokens from the restricted pool despite never being individually allowlisted.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
