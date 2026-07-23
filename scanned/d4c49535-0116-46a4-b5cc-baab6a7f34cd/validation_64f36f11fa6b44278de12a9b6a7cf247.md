### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User — Allowlist Fully Bypassed via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the actual end-user. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed), every unprivileged user can bypass the curated allowlist by simply calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without forwarding the real caller: [4](#0-3) 

The router stores the real `msg.sender` only in transient storage for payment settlement — it is never surfaced to the pool or the extension: [5](#0-4) 

The result is that the extension sees `sender = address(router)`, not the actual trader. The pool admin faces an inescapable dilemma:

| Admin choice | Consequence |
|---|---|
| Allowlist the router | Every user on the internet can bypass the allowlist |
| Do not allowlist the router | Allowlisted users cannot use the router at all |

The same wrong-actor binding exists in the multi-hop `exactInput` path for intermediate hops, where `sender` becomes `address(this)` (the router itself): [6](#0-5) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or institutional counterparties is rendered completely ineffective. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`) targeting the restricted pool and execute swaps that the allowlist was designed to block. This constitutes a direct, fund-impacting policy bypass: the pool's LP assets are exposed to counterparties the pool admin explicitly excluded, and any fee or risk model predicated on a closed participant set is violated.

---

### Likelihood Explanation

Likelihood is **High**:

1. `MetricOmmSimpleRouter` is the primary user-facing swap interface; pool admins are expected to support it.
2. To allow their allowlisted users to trade through the router, admins must allowlist the router address — the exact condition that opens the bypass to everyone.
3. No special privilege, flash loan, or unusual token behavior is required. Any EOA can call the router.
4. The `generate_scanned_questions.py` audit scaffold explicitly flags this path as a priority target, confirming the protocol designers were aware the identity check must survive router indirection. [7](#0-6) 

---

### Recommendation

The pool's `swap` function should accept an explicit `swapper` parameter (the real end-user) that the router populates with `msg.sender` before calling the pool. Alternatively, `SwapAllowlistExtension` should be redesigned to read the real payer from a trusted transient-storage slot written by the router, analogous to how the liquidity adder stores its payer context. The current architecture where `sender = pool's msg.sender` is structurally incompatible with any router-mediated allowlist enforcement.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  2. Admin calls setAllowedToSwap(pool, alice, true)       // alice is the only allowed trader
  3. Admin calls setAllowedToSwap(pool, router, true)      // required so alice can use the router
  4. alice adds liquidity.

Attack (executed by bob, who is NOT allowlisted):
  5. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     });

Trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...)          // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives tokens

Result: bob, a non-allowlisted user, successfully swaps on the curated pool.
        The allowlist invariant is broken.
``` [8](#0-7) [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
