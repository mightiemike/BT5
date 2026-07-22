### Title
SwapAllowlistExtension gates the router address instead of the economic actor, allowing any user to bypass the per-pool swap allowlist via the router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, that `msg.sender` is the **router contract**, not the originating user. If the pool admin allowlists the router (a natural action to enable router-based swaps for their permitted users), every unpermissioned user can bypass the allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

Therefore the allowlist lookup becomes `allowedSwapper[pool][router]`. If the admin allowlists the router address — a natural step when they want their permitted users to be able to use the router — the check passes for **every** user who routes through the router, regardless of whether that user is individually permitted.

The `DepositAllowlistExtension` does not share this flaw because it checks the explicitly-passed `owner` argument (the position owner), not the direct caller: [6](#0-5) 

### Impact Explanation
Any user who is **not** on the swap allowlist can execute swaps against a restricted pool by calling `MetricOmmSimpleRouter` whenever the router address is itself allowlisted. This completely nullifies the access-control guarantee of `SwapAllowlistExtension`: unauthorized users can drain liquidity, front-run oracle moves, or trade in pools that were intended to be restricted to a specific set of counterparties. The loss is direct — pool LP assets are exchanged at oracle prices with an unpermissioned counterparty.

### Likelihood Explanation
The scenario is reachable without any privileged or malicious setup:

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading to known counterparties.
2. Admin allowlists specific user addresses **and** the router address so those users can interact via the standard periphery.
3. Any unpermissioned user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting that pool.
4. The allowlist check resolves to `allowedSwapper[pool][router] == true` and passes.
5. The swap executes at the oracle price against the restricted pool's liquidity.

Step 2 is the natural, expected admin action — without it, even allowlisted users cannot use the router. The bypass is therefore a predictable consequence of normal pool configuration.

### Recommendation
The allowlist must gate the **economic actor** (the originating user), not the intermediary contract. Two complementary fixes:

1. **Router-side**: Have the router encode the originating `msg.sender` into `extensionData` for every hop, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when present.
2. **Extension-side**: Document clearly that allowlisting any shared intermediary (router, multicall) is equivalent to opening the pool to all users, and add a NatSpec warning to `setAllowedToSwap`.

A stricter alternative is to check both `sender` (direct caller) and a user address decoded from `extensionData`, requiring the router to attest the originating user and the extension to verify the attestation.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls swapExtension.setAllowedToSwap(pool, alice, true)
  admin calls swapExtension.setAllowedToSwap(pool, router, true)   // to let alice use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  pool.swap(msg.sender=router, ...)
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router)
    → allowedSwapper[pool][router] == true  ✓  (check passes)
    → swap executes, bob receives tokens from the restricted pool
```

`alice` is the intended beneficiary of the allowlist; `bob` bypasses it entirely because the router — not `bob` — is the identity the extension sees.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
