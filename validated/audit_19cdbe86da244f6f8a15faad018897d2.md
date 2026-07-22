### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` mediates the swap, `msg.sender` at the pool is the router contract, not the original user. If the pool admin allowlists the router to support router-mediated swaps for legitimate users, any unprivileged user can bypass the allowlist entirely by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` (the immediate caller of `pool.swap()`) as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level: [3](#0-2) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. In every router-mediated path, the pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. [4](#0-3) 

The pool admin faces an impossible choice:

- **Router not allowlisted:** Allowlisted users cannot use the router at all; every router-mediated swap reverts with `NotAllowedToSwap`.
- **Router allowlisted:** The allowlist is completely bypassed — any unprivileged user calls the router, the extension sees the allowlisted router address, and the swap proceeds.

The `SwapAllowlistExtension` is designed to gate the economically relevant actor (the swapper), but the router severs the identity link between the original caller and the pool-level `msg.sender`.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted protocols) can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. The attacker receives pool output tokens at the pool's oracle-derived price, draining LP assets or extracting value at terms the pool admin intended to reserve for authorized parties only. This is a direct loss of LP principal and a complete failure of the access-control invariant the extension is designed to enforce.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entrypoint documented in the periphery. Any user who discovers that a pool uses `SwapAllowlistExtension` and that the router is allowlisted can immediately exploit the bypass with a single `exactInputSingle` call. No special privileges, flash loans, or multi-step setup are required. The router is a deployed, immutable public contract.

### Recommendation

The `SwapAllowlistExtension` must gate the original user, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Check `recipient` instead of `sender`:** If the pool's design guarantees that `recipient` is always the economic beneficiary, the extension can check `allowedSwapper[pool][recipient]`. However, this may not hold for multi-hop paths where intermediate recipients are the router itself.
3. **Dedicated router-aware extension:** Deploy a variant of `SwapAllowlistExtension` that accepts a signed proof of the original caller's identity forwarded by the router.

### Proof of Concept

```
Setup:
  pool = Pool with SwapAllowlistExtension configured
  pool admin: allowedSwapper[pool][alice] = true
  pool admin: allowedSwapper[pool][router] = true  ← required for alice to use router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
          msg.sender at pool = router
        → _beforeSwap(router, bob, ...)
          → SwapAllowlistExtension.beforeSwap(router, bob, ...)
              allowedSwapper[pool][router] == true  ← passes
        → swap executes, bob receives output tokens

Result:
  bob successfully swaps on a pool he is not allowlisted for.
  The SwapAllowlistExtension guard is silently bypassed.
``` [5](#0-4) [6](#0-5)

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
