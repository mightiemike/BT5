Audit Report

## Title
SwapAllowlistExtension gates on the router address instead of the actual user when swaps are routed through MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against `allowedSwapper[pool][sender]`, where `sender` is whatever `msg.sender` the pool received when `swap` was called. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. This creates two fund-impacting consequences: allowlist bypass for any user routing through an allowlisted router, and broken core swap functionality for allowlisted users who use the router.

## Finding Description

`MetricOmmPool.swap` unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (correct key) and `sender` is the value passed from the pool — which is the router, not the end-user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

For multi-hop `exactInput`, intermediate hops use `address(this)` (the router) as both payer and recipient, compounding the wrong-actor binding: [5](#0-4) 

The exact wrong value is `allowedSwapper[pool][router]` being evaluated in place of `allowedSwapper[pool][actualUser]`. No existing guard corrects this: the pool has no mechanism to pass the originating user separately from `msg.sender`, and the extension has no fallback to `tx.origin` or any other identity source.

## Impact Explanation

**Path A — Allowlist bypass (High):** A pool admin who allowlists the router to enable standard periphery access causes `allowedSwapper[pool][router] == true`. Any unprivileged user — including those the admin explicitly never allowlisted — can call `router.exactInputSingle(...)` and pass the guard, fully defeating the curated pool's access control for all router-mediated volume.

**Path B — Broken core swap functionality (Medium):** A pool admin who allowlists specific end-users (e.g., KYC'd addresses) but not the router will find that those users cannot use the router at all. The extension sees `sender = router`, finds it not allowlisted, and reverts `NotAllowedToSwap`, locking authorized users out of the standard periphery path. This is broken core swap functionality for authorized users.

Both paths meet the allowed impact gate: Path A is a direct allowlist bypass enabling unauthorized trading; Path B is broken core swap functionality causing loss of access for authorized users.

## Likelihood Explanation

The trigger is fully unprivileged. Path A requires only that the pool admin has allowlisted the router — a natural and expected action for any pool that intends to support the standard periphery. Path B is triggered by any allowlisted user simply using the router. Neither path requires malicious setup, special roles, non-standard tokens, or any privileged action by the attacker.

## Recommendation

The extension must gate on the actual end-user, not the intermediary. The cleanest fix is to pass the originating user explicitly through the swap call chain:

1. Add a `swapper` parameter (separate from `recipient`) to `IMetricOmmPoolActions.swap`, set by the router to `msg.sender` before forwarding to the pool.
2. Have the pool pass this `swapper` value as `sender` to `_beforeSwap` instead of `msg.sender`.
3. Alternatively, the router can store the originating user in transient storage (analogous to how it already stores the payer via `_setNextCallbackContext`) and the pool reads it to populate the `sender` field forwarded to extensions. [6](#0-5) 

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     — natural action to enable router-mediated swaps.
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack (Path A — bypass):
  4. attacker calls router.exactInputSingle({pool: pool, recipient: attacker, ...})
  5. Router calls pool.swap(attacker, zeroForOne, amount, limit, "", extensionData)
     → pool's msg.sender = router
  6. Pool calls _beforeSwap(router /*sender*/, attacker /*recipient*/, ...)
  7. Extension evaluates: allowedSwapper[pool][router] == true → guard passes
  8. Swap executes. attacker receives output tokens.
  Expected: revert NotAllowedToSwap
  Actual:   swap succeeds — allowlist fully bypassed.

Attack (Path B — broken functionality):
  1. Pool admin calls setAllowedToSwap(pool, alice, true)
     — does NOT allowlist the router.
  2. alice calls router.exactInputSingle({pool: pool, ...})
  3. Extension evaluates: allowedSwapper[pool][router] == false → reverts NotAllowedToSwap
  Expected: swap succeeds (alice is authorized)
  Actual:   revert — alice is locked out of the standard periphery path.
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
