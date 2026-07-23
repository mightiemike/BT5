### Title
`SwapAllowlistExtension` checks the router's address instead of the actual user's address, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` the extension sees — is the **router contract**, not the actual user. The allowlist check is applied to the wrong actor: the router is checked instead of the human swapper. If the router is allowlisted (or `allowAllSwappers` is set), any user can bypass per-user restrictions by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks `sender` (the immediate pool caller).**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool and `sender` is the argument the pool forwarded — i.e., whoever called `pool.swap`. [3](#0-2) 

**Step 3 — `MetricOmmSimpleRouter` is the `msg.sender` of `pool.swap`, not the user.**

`exactInputSingle` calls `pool.swap(...)` directly; the pool sees `msg.sender = router`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result — the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.**

Two broken outcomes follow:

| Router allowlist state | Effect |
|---|---|
| Router is allowlisted | **Any** user bypasses the per-user allowlist by routing through the public router |
| Router is not allowlisted | **All** allowlisted users are blocked from using the router |

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` — the position owner explicitly passed as a separate parameter to `addLiquidity` — rather than `sender` (the payer/operator): [6](#0-5) 

For deposits, `owner` and `sender` are distinct parameters and the extension correctly gates on `owner`. For swaps, no equivalent "actual user" parameter exists in the hook signature, so the extension is forced to use `sender`, which collapses to the router when the router is used. [7](#0-6) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties). The admin allowlists individual user addresses. A non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` targeting that pool. The pool sees `sender = router`. If the router is allowlisted (a natural operational choice to let legitimate users trade via the standard interface), the extension passes for every caller regardless of their individual allowlist status. The curation policy is completely bypassed, and unauthorized users can extract value from the pool or trade against restricted liquidity. This is a direct loss of the pool's access-control invariant with fund-impacting consequences.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that configures `SwapAllowlistExtension` and expects users to interact via the router is affected. The bypass requires no special privileges — any public user can call `exactInputSingle`. The only precondition is that the router is allowlisted (or `allowAllSwappers` is set), which is the expected operational state for a pool that wants to support router-based trading.

---

### Recommendation

The `beforeSwap` hook signature must carry the actual end-user identity separately from the immediate pool caller. Two viable approaches:

1. **`extensionData` convention**: Require the router to ABI-encode the actual user's address as the first word of `extensionData` for allowlist-gated pools, and have `SwapAllowlistExtension` decode and check that address when `sender` is a known router.

2. **Hook signature extension**: Add an `originator` field to `IMetricOmmExtensions.beforeSwap` (analogous to `owner` in `beforeAddLiquidity`) that the pool populates from a trusted source, and have the router forward `msg.sender` into that field via `callbackData`/transient storage before calling `pool.swap`.

The minimal safe fix for the current interface is to check `sender` **and** reject calls where `sender` is any registered periphery contract unless the actual user is separately verified — mirroring how `DepositAllowlistExtension` separates `owner` from `sender`.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow the router as a trusted intermediary.
3. Pool admin calls setAllowedToSwap(pool, alice, true)
   — alice is the only allowlisted user.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient, zeroForOne, amount, ...).
6. Pool calls _beforeSwap(msg.sender=router, ...).
7. SwapAllowlistExtension.beforeSwap(sender=router, ...) checks allowedSwapper[pool][router] → true.
8. Swap executes. Bob bypassed the allowlist entirely.
```

The root cause is identical in structure to the PaprController M-06 finding: the wrong actor (`router` / `PaprController`) is substituted for the intended actor (`user` / `msg.sender`) in a guarded operation, because the call indirection is not accounted for in the check.

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
