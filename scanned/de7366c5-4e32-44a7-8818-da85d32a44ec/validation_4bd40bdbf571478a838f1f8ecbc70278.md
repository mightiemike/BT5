### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. A pool admin who allowlists the router (to enable router-based swaps for permitted users) inadvertently opens the pool to every user, because all router-mediated swaps appear as the router address to the extension.

### Finding Description

The pool's `swap` function passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol::swap
_beforeSwap(
    msg.sender,   // <-- the immediate caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks this `sender` argument against the per-pool allowlist, keyed by `msg.sender` (the pool):

```solidity
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

At the pool, `msg.sender` is the **router address**. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The end user's identity is only present in `recipient`, which the extension ignores.

The same actor-binding mismatch exists for `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`. [4](#0-3) 

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` intends to gate individual users. To allow permitted users to also swap via the router, the admin must allowlist the router address. Once the router is allowlisted, **every user** — including those explicitly not on the allowlist — can bypass the gate by routing through `MetricOmmSimpleRouter`. The curated pool's swap restriction is completely nullified for any user who knows to use the router. This allows unauthorized users to trade against LP funds on a pool that was designed to be restricted, constituting a direct policy bypass with fund-impacting consequences (unauthorized price impact on LP positions, unauthorized extraction of LP value).

### Likelihood Explanation

The trigger is unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The only precondition is that the pool admin has allowlisted the router — a natural and expected operational step for any pool that wants to support the standard periphery. No special permissions, malicious setup, or non-standard tokens are required.

### Recommendation

The `SwapAllowlistExtension` must gate the **economic actor**, not the immediate caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData`, and the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: If the pool's design consistently routes output to the economic actor, the extension can check `recipient`. However, this may not hold for all swap configurations.

3. **Preferred — dedicated router-aware check**: The extension should expose a separate allowlist for "operator" addresses (like the router) that are trusted to forward the real user identity, and require the real user identity to be passed and verified via `extensionData`.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin allowlists only `alice` directly: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin also allowlists the router: `setAllowedToSwap(pool, router, true)` (to let alice use the router).
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. `beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `bob` successfully swaps on a pool he was explicitly excluded from.

The extension's `sender` check passes because it sees the router, not `bob`: [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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
