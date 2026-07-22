### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing any user to bypass a curated pool's per-user swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every user — including non-allowlisted ones — can bypass the per-user gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension caller), and `sender` is the first argument forwarded by the pool — which the pool sets to its own `msg.sender`, i.e. whoever called `pool.swap()`. [1](#0-0) 

The pool passes `msg.sender` as `sender` to both `_beforeSwap` and `_afterSwap`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

So `msg.sender` inside `pool.swap()` is the router, and the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The same pattern applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants allowlisted users to be able to use the router must allowlist the router contract itself. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every call that arrives through the router — regardless of who the actual user is. Any non-allowlisted address can then call `router.exactInputSingle(pool=curatedPool, ...)` and the extension passes, bypassing the per-user gate entirely. The curated pool's swap restriction is rendered ineffective for all router-mediated paths.

This is a direct policy bypass: the pool was configured to restrict trading to specific addresses, but any address can trade by routing through the supported periphery contract.

---

### Likelihood Explanation

The trigger is a normal, non-privileged user action (calling the public router). The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration step for any admin who wants allowlisted users to access the pool through the standard periphery. There is no malicious setup assumption; the admin acts in good faith. The bypass is reachable on every pool that has both `SwapAllowlistExtension` configured and the router allowlisted.

---

### Recommendation

The extension should check the actual economic actor, not the immediate caller. Two options:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives output tokens and is the economically relevant party for a swap.
2. **Require the actual user identity to be passed via `extensionData`** and verified against a signature or trusted forwarder pattern, so the router can attest to the real caller.

The simplest fix consistent with the existing design is to gate on `recipient` (the second argument to `beforeSwap`), since the router always sets `recipient` to the actual user:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

---

### Proof of Concept

```
Setup:
  pool = curated MetricOmmPool with SwapAllowlistExtension
  admin allowlists: allowedSwapper[pool][alice] = true
  admin allowlists: allowedSwapper[pool][router] = true  ← needed for alice to use router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Flow:
    router → pool.swap(recipient=bob, ...)
    pool → extension.beforeSwap(sender=router, recipient=bob, ...)
    extension checks: allowedSwapper[pool][router] == true  ✓
    swap executes for bob despite bob not being allowlisted

Result:
  bob trades on a pool that was supposed to restrict him.
  The per-user allowlist is completely bypassed for all router-mediated swaps.
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
