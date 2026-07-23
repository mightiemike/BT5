### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on `sender`, which is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router contract**, not the end user. If the pool admin allowlists the router (required for any router-based swap to succeed), every user — including those not on the allowlist — can bypass the curated-pool restriction by calling the router instead of the pool directly.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`_beforeSwap` forwards `sender` (= `msg.sender` of the pool call) to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the end user. The allowlist check becomes `allowedSwapper[pool][router]`. If the pool admin adds the router to the allowlist (the only way to permit any router-based swap), the gate is wide open for every user who calls through the router.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` — the explicit position-owner address passed through the call — so the deposit guard is not affected: [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → legitimate allowlisted users cannot use the router at all.
- **Allowlist the router** → every user, including those explicitly excluded, can swap freely.

Either outcome breaks the core invariant that only approved addresses may trade in the pool. Disallowed users can drain pool liquidity at oracle-quoted prices, causing direct loss of LP principal and fee revenue.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap entrypoint for EOAs. Any pool admin who deploys `SwapAllowlistExtension` and also wants router support will naturally allowlist the router, triggering the bypass. The attacker needs no special privilege — a single call to `exactInputSingle` with the pool as target suffices.

---

### Recommendation

Pass the **end user** identity through the swap path rather than the immediate caller. Two options:

1. **Preferred**: Change `beforeSwap`'s actor check to use `recipient` (the address receiving output tokens) when the pool is called via a trusted router, or add a dedicated `swapInitiator` field to the extension call that the pool populates from transient storage set by the router (analogous to how `MetricOmmSwapRouterBase` already stores payer context in transient storage).

2. **Simpler**: Document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at the factory level by reverting pool creation when both are configured together.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  - Pool admin calls setAllowedToSwap(pool, router, true)  // needed for router swaps

Attack (bob, not on allowlist):
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient=bob, ...)
     → msg.sender of pool.swap = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob receives tokens despite not being on the allowlist

Result: allowlist is fully bypassed; bob trades in a curated pool without authorization.
``` [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
