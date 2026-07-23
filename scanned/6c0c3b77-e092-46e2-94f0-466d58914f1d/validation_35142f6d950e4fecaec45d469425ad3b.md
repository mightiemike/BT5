### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router address to enable router-mediated swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the same public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows: [1](#0-0) 

`msg.sender` inside the extension is the pool (the pool calls the extension). `sender` is the first argument forwarded by the pool, which the pool sets to its own `msg.sender`: [2](#0-1) 

`_beforeSwap` is called with `msg.sender` as the first argument: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutput`) calls the pool, the pool's `msg.sender` is the router contract: [4](#0-3) 

For multi-hop `exactInput`, every hop is called from the router, so every pool in the path sees `sender = router`: [5](#0-4) 

The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address itself. Once the router is allowlisted, the check passes for every caller of the router regardless of their individual allowlist status.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties is fully bypassed. Any unpermitted user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`/`exactOutput`) targeting the restricted pool. The extension sees `sender = router`, which is allowlisted, and permits the swap. The unpermitted user executes trades on a pool that was designed to exclude them, receiving pool output tokens and depleting LP reserves that were reserved for permitted counterparties. This is a direct loss of LP assets and a broken core pool invariant (access-controlled swap execution).

### Likelihood Explanation

The router is the standard public entry point for swaps. Any pool admin who wants their allowlisted users to be able to use the router (the normal UX path) must allowlist the router. This is the expected operational configuration for any curated pool that does not require users to call the pool directly. The attacker needs no special privilege: they call a public function on a public contract with no preconditions beyond having the input token.

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor, not the immediate pool caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply the correct value, which is acceptable since the router is a known periphery contract.

2. **Check `sender` against the allowlist but also accept the router as a transparent forwarder**: Add a separate `allowedForwarder` mapping. If `sender` is an allowlisted forwarder (e.g., the router), decode the real user from `extensionData` and check that address instead.

Either approach must be applied consistently to `exactInputSingle`, `exactInput`, `exactOutput`, and `exactOutputSingle` router paths.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for their permitted users.
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...})`.
4. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)`. Pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(router, ...)`. Extension checks `allowedSwapper[pool][router]` → `true` → no revert.
6. Swap executes. Attacker receives output tokens from a pool they were not permitted to trade on. [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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
```
