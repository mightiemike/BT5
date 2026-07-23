### Title
SwapAllowlistExtension Gates the Router Contract Instead of the Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the originating EOA. If the pool admin allowlists the router (the only way to enable standard periphery usage), every user — including those the admin intended to block — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking the `sender` argument against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool: [1](#0-0) 

The pool populates `sender` with its own `msg.sender` — the direct caller of `pool.swap()`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput` (all hops) and `exactOutput` (all recursive hops): [5](#0-4) 

Therefore, the extension always sees `sender = router_address` for every router-mediated swap, regardless of which EOA initiated the transaction. The allowlist is effectively keyed on the router contract, not on the actual economic actor.

This creates two mutually exclusive broken states:

1. **Allowlist bypass:** The pool admin allowlists the router address (the only way to permit standard periphery usage). Any EOA — including those the admin intended to block — can now swap freely by calling the router.
2. **Broken core functionality:** The pool admin allowlists specific EOAs. Those EOAs cannot use the router at all; they must call `pool.swap()` directly. The standard periphery path is unusable for any allowlisted user.

The `DepositAllowlistExtension` does **not** share this flaw because it checks `owner` (the position owner explicitly passed by the caller), not `sender`: [6](#0-5) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to approved counterparties loses that protection entirely the moment the router is allowlisted. Any unpermissioned user can execute swaps against the pool's LP assets by routing through `MetricOmmSimpleRouter`, directly violating the pool's curation policy and exposing LP funds to unauthorized trading activity. This is a **direct loss of curation control** and constitutes broken core pool functionality (the allowlist guard silently fails open on the standard periphery path).

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool that (a) deploys `SwapAllowlistExtension` and (b) wants to support normal periphery usage must allowlist the router, immediately enabling the bypass. The attacker requires no special privileges, no malicious setup, and no non-standard tokens — only a call to the public `exactInputSingle` or `exactInput` router function.

---

### Recommendation

The extension must gate the **originating EOA**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the original caller through the router.** The router stores `msg.sender` in transient storage (it already does this for the callback payer via `_setNextCallbackContext`). The pool could read this value and forward it as a separate `originalSender` argument to extensions, or the extension could read it from a well-known transient slot.

2. **Check `recipient` instead of `sender` for swap allowlisting.** The `recipient` is the address that receives output tokens and is the economically meaningful actor. It is already forwarded correctly through the router (`params.recipient`). The extension would then check `allowedSwapper[pool][recipient]`.

Either way, the invariant that must hold is: **the identity checked by the allowlist must be the same actor the pool admin intended to gate, regardless of which supported public entrypoint reaches the pool.**

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, router, true)` — the only way to enable router-mediated swaps.
3. Admin does **not** allowlist `mallory` (an unauthorized EOA).
4. `mallory` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → passes.
8. `mallory` successfully swaps against the curated pool, bypassing the allowlist entirely.

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
