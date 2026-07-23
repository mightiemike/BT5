Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` executes a swap, it calls `pool.swap()` directly, so the pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`, making the per-user allowlist structurally unenforceable for all router-mediated swaps.

## Finding Description
In `SwapAllowlistExtension.beforeSwap`, the guard is:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`. [1](#0-0) 

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to the extension: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly. The actual user's address is stored only in transient storage via `_setNextCallbackContext` for the payment callback and is never forwarded to the pool or extension: [4](#0-3) 

The same identity mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) 

The pool always sees `msg.sender = router`, so `sender = router` reaches `beforeSwap`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. No existing guard in the extension, pool, or router corrects this identity mismatch.

## Impact Explanation
A pool admin deploying `SwapAllowlistExtension` intends to gate swaps to a specific set of addresses (e.g., KYC-verified traders). The `allowedSwapper` mapping expresses per-user intent keyed by `(pool, swapper)`. However, the admin faces an impossible choice: not allowlisting the router blocks all router users including individually allowlisted ones; allowlisting the router allows every on-chain user to bypass the per-user allowlist via a single `exactInputSingle` call. There is no configuration that allows specific users to swap through the router while blocking others. This constitutes broken core pool functionality — the allowlist extension's access control is rendered ineffective for all router-mediated swaps, allowing unauthorized users (e.g., non-KYC addresses) to drain pool liquidity or execute trades the admin intended to block.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical public swap interface. Any user who discovers the allowlist can trivially route through the router with no special privileges, flash loans, or oracle manipulation — a single `exactInputSingle` call suffices. The bypass is repeatable and unconditional whenever the router is allowlisted.

## Recommendation
Pass the originating user's address through the router to the pool (e.g., via `extensionData` or a dedicated field in the swap parameters), and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known trusted router. Alternatively, the extension can maintain a separate allowlist for trusted routers and require that the router itself enforces per-user checks before calling the pool.

## Proof of Concept
1. Pool is deployed with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`. `allowAllSwappers[pool] = false`. Only `alice` is in `allowedSwapper[pool]`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can use the router.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
5. Pool calls `ExtensionCalling._beforeSwap(sender=router, ...)`, which calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `bob` successfully swaps in a pool he was explicitly excluded from, bypassing the allowlist guard entirely.

A Foundry integration test can reproduce this by: deploying the pool with the extension, setting `allowedSwapper[pool][alice] = true` and `allowedSwapper[pool][router] = true`, then calling `exactInputSingle` from a `bob` address and asserting the swap succeeds rather than reverting with `NotAllowedToSwap`.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
