Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the real user, allowing any caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router (the natural operational step to let approved users reach the pool via the periphery), every unpermissioned caller of the router bypasses the gate, exposing LP liquidity to unauthorized counterparties.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)   // sender == pool's msg.sender
    )
);
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [4](#0-3) 

The pool's `msg.sender` is now the router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`. If the pool admin has allowlisted the router address, the check passes for every caller of the router, regardless of whether that caller is individually approved. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` entry points. [5](#0-4) 

## Impact Explanation
Any user who is not on the allowlist can execute swaps against a curated pool by routing through `MetricOmmSimpleRouter`. The pool's LP liquidity is exposed to unauthorized counterparties. For institutional-only pools or pools with favorable oracle pricing for specific counterparties, this results in direct loss of LP principal or protocol fees and constitutes a broken core pool invariant — the allowlist gate is rendered ineffective.

## Likelihood Explanation
The bypass requires the pool admin to have allowlisted the router. This is the expected operational step: without it, even individually approved users cannot use the router to reach the pool. The admin is therefore incentivized to allowlist the router, unknowingly opening the gate to all users. The trigger is a single unpermissioned call to any `exact*` entry point on `MetricOmmSimpleRouter`.

## Recommendation
The allowlist check must be keyed to the economically relevant actor, not the intermediary. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it when present. This requires a coordinated convention between router and extension.
2. **Exclude the router from allowlisted pools**: Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router and must require users to call the pool directly.

The cleaner fix is option 1, where the router always appends the real originator to `extensionData` and the extension verifies it when present.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — the natural step to let approved users use the periphery.
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Attacker successfully swaps against the curated pool without being on the allowlist.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
