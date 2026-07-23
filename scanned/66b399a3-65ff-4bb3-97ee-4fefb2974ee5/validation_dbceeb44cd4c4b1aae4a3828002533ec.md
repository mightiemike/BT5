### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass Curated-Pool Swap Restrictions via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the actual user. A pool admin who allowlists the router to enable router-based swaps for permitted users simultaneously grants every unpermitted user the ability to bypass the allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the call to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — so the pool sees `msg.sender = router`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Consequence:** The extension's allowlist entry that the pool evaluates is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who wants allowlisted users to be able to use the router must add the router address to the allowlist. Once the router is allowlisted, every user — including those explicitly not permitted — can bypass the restriction by calling any of the router's swap functions. The extension cannot distinguish between different users sharing the same router intermediary.

The `DepositAllowlistExtension` does **not** share this flaw: it checks `owner` (the second parameter), which `MetricOmmPoolLiquidityAdder` sets to the actual position owner address, not the adder contract itself. [6](#0-5) [7](#0-6) 

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties, institutional traders, or whitelisted protocols) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The bypassing user can execute real swaps, receiving pool output tokens and paying input tokens, with no loss of funds to themselves and a direct violation of the pool's access-control invariant. LP assets are exposed to trades from actors the pool admin explicitly excluded.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical periphery swap entry point. Any user aware of the allowlist restriction can trivially call `exactInputSingle` or `exactInput` on the router instead of calling `pool.swap` directly. No special privileges, flash loans, or multi-step setup are required. The only precondition is that the pool admin has allowlisted the router — a step they must take if they want any of their permitted users to use the standard router UX.

---

### Recommendation

The extension must bind the allowlist check to the actual economic actor, not the intermediary. Two complementary approaches:

1. **Pass the real user through the router.** Add a `payer`/`originator` field to the extension data that the router populates with `msg.sender` before calling the pool, and have `SwapAllowlistExtension` decode and check that field instead of (or in addition to) `sender`.

2. **Check `recipient` as a proxy.** If the pool's design guarantees that `recipient` is always the benefiting user, the extension can gate on `recipient` instead of `sender`. This is weaker because `recipient` can be set to any address.

3. **Document that the allowlist gates direct callers only** and explicitly warn pool admins never to allowlist shared router contracts, accepting that router-based swaps are unavailable on curated pools.

The cleanest fix is option 1: extend the `extensionData` ABI to carry a verified originator that the router sets to `msg.sender`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (to enable router-based swaps for permitted users).
  - Pool admin does NOT call swapExtension.setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({
          pool: pool,
          tokenIn: token0,
          tokenOut: token1,
          zeroForOne: true,
          amountIn: X,
          ...
      })
  - router calls pool.swap(recipient, ...) → pool sees msg.sender = router.
  - pool calls _beforeSwap(router, recipient, ...).
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  - Swap executes. attacker receives token1 output.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; attacker bypasses the per-user allowlist.
``` [8](#0-7) [9](#0-8)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L64-68)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
