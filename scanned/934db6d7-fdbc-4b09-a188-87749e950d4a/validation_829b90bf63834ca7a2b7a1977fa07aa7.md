### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. If the pool admin allowlists the router (the only way to permit any router-mediated swap), every non-allowlisted user can bypass the curated-pool restriction by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed in: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool see `msg.sender = router`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — every router entry point calls `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

**The dilemma the pool admin faces:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all; they must implement `IMetricOmmSwapCallback` themselves |
| **Allowlist the router** | Every non-allowlisted user can bypass the restriction by calling any router entry point |

There is no configuration that simultaneously permits router-mediated swaps and enforces per-user allowlist policy.

The `DepositAllowlistExtension` does **not** share this flaw: it checks the `owner` parameter (the position owner explicitly supplied to `addLiquidity`), which the `MetricOmmPoolLiquidityAdder` correctly sets to the actual beneficiary. [6](#0-5) 

---

### Impact Explanation

A non-allowlisted user on a curated pool that has `SwapAllowlistExtension` active can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The pool sees `sender = router_address`; if the router is allowlisted, the check passes and the swap executes. The user receives output tokens they were explicitly barred from obtaining, breaking the core access-control invariant of the curated pool. Depending on pool composition and oracle pricing, this can result in direct extraction of LP value by unauthorized parties.

---

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router — a natural and expected action for any pool that intends to support the standard periphery UX. Any user who discovers the allowlisted router address can exploit this immediately with a single public transaction. No privileged access, no special token, and no multi-step setup is required beyond knowing the router address (which is a public deployment).

---

### Recommendation

Pass the **original caller identity** through the swap path so the extension can gate on the actual economic actor. Two complementary approaches:

1. **Add an `originator` field to the `swap` signature** (or to `extensionData`) that the router populates with `msg.sender` before calling the pool. The extension then checks `originator` instead of `sender`.

2. **Check `sender` against the router and then verify the originator from transient storage** — the router already stores the payer in transient storage (`_getPayer()`); expose that as a readable field the extension can query.

Either way, `SwapAllowlistExtension.beforeSwap` must resolve to the address that initiated the user-facing transaction, not the intermediate contract that called `pool.swap()`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]   = true   (alice is the intended gated user)
  allowedSwapper[pool][router]  = true   (admin allowlists router for UX)
  allowedSwapper[pool][attacker] = false  (attacker is explicitly excluded)

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      pool,
      recipient: attacker,
      ...
  })

  pool.swap(recipient=attacker, ...) is called with msg.sender = router
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → passes
  swap executes; attacker receives output tokens

Result: attacker bypasses the swap allowlist and trades on a curated pool.
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
