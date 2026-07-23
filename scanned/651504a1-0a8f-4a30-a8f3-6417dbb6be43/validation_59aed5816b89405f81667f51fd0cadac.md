### Title
`SwapAllowlistExtension` checks router address instead of originating user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the originating user. If the pool admin allowlists the router to enable router-mediated swaps for curated users, any non-allowlisted user can bypass the gate by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who wants to support both direct and router-mediated swaps for allowlisted users must allowlist the router address. Once the router is allowlisted, every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through `MetricOmmSimpleRouter`.

The project's own audit-target specification identifies this exact invariant:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [6](#0-5) 

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional, or regulatory-restricted) that relies on `SwapAllowlistExtension` for access control is fully open to any user who calls `MetricOmmSimpleRouter`. The attacker receives real token output from the pool's LP reserves; the pool's LP providers bear the counterparty exposure they explicitly configured the allowlist to prevent. This is a direct loss of the curation guarantee and constitutes a broken core pool functionality / admin-boundary break with fund-impacting consequences.

---

### Likelihood Explanation

The trigger is entirely unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle` with a curated pool address. The only precondition is that the pool admin has allowlisted the router (a natural configuration step for any pool that wants to support the standard periphery). No special timing, flash loan, or oracle manipulation is required.

---

### Recommendation

The extension must verify the **originating user**, not the intermediate caller. Two sound approaches:

1. **Router-forwarded identity in `extensionData`**: The router encodes `msg.sender` (the originating user) into `extensionData` for each hop. The extension decodes and checks that address. The extension must also verify that the claim comes from a trusted router (e.g., via a factory-registered router registry), otherwise any caller can forge the field.

2. **Direct-call-only allowlist**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert in `beforeSwap` when `sender != tx.origin` (EOA-only guard). This is simpler but prevents contract-based allowlisted callers.

The current design has no safe middle ground: allowlisting the router opens the gate to everyone; not allowlisting it silently breaks router access for legitimate allowlisted users.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured on BEFORE_SWAP_ORDER.
2. Admin calls swapExtension.setAllowedToSwap(pool, alice, true)       // alice is KYC'd
3. Admin calls swapExtension.setAllowedToSwap(pool, router, true)      // enable router path
4. bob (not allowlisted) calls:
       router.exactInputSingle({
           pool:       pool,
           recipient:  bob,
           zeroForOne: true,
           amountIn:   1000,
           ...
       })
5. Router calls pool.swap(bob, true, 1000, ...) — msg.sender at pool = router.
6. Pool calls _beforeSwap(router, bob, ...).
7. Extension checks allowedSwapper[pool][router] == true  →  passes.
8. Bob's swap executes and he receives token1 from the curated pool.
   The allowlist check on bob's address is never performed.
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
