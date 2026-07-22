### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks whether the **router** is allowlisted rather than the actual user. If the router is allowlisted (the natural operational state for a pool that wants to support the standard periphery), any unprivileged user can bypass the curated allowlist entirely by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool — i.e., the direct caller of `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without passing the originating user's address anywhere in the call: [4](#0-3) 

Because the router is the direct caller of `pool.swap`, the pool's `msg.sender` is the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual user's address is never presented to the guard.

The same misbinding applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

---

### Impact Explanation

**Scenario A — Router is allowlisted (intended operational state):**  
A pool admin who wants users to reach the pool through the standard periphery must allowlist the router. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the guard passes for every call that arrives through the router regardless of who the originating user is. Any address — including addresses the admin explicitly excluded — can bypass the curated allowlist by calling `MetricOmmSimpleRouter.exactInputSingle`. The allowlist provides zero protection.

**Scenario B — Router is not allowlisted:**  
Addresses that are individually allowlisted cannot use the router at all; they must call `pool.swap` directly. The standard periphery path is broken for every allowlisted user on every curated pool.

Both scenarios represent a broken core invariant. Scenario A is the higher-severity path: it is a complete, unprivileged bypass of a configured access-control guard with direct fund-impact consequences (disallowed traders execute swaps on a pool that was designed to exclude them).

---

### Likelihood Explanation

The router is the canonical, documented entry point for swaps. Any pool that configures `SwapAllowlistExtension` and also expects users to use the router will, in practice, be in Scenario A. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call. Every public user can reach it.

---

### Recommendation

The pool's `swap` interface does not carry an explicit `sender` parameter, so the fix must be applied at the periphery layer. Two options:

1. **Add an explicit `sender` parameter to `pool.swap`** so the router can forward `msg.sender` (the actual user) and the extension receives the correct identity.
2. **Have the router encode the originating user inside `extensionData`** and have `SwapAllowlistExtension` decode and verify it — but this requires the extension to trust the router, which reintroduces a trust assumption.

Option 1 is the cleaner fix and mirrors the resolution applied in the Gearbox analog (passing `msg.sender` instead of the wrong variable).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router must be allowed for normal use
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — pool sees msg.sender = router
  - Pool calls _beforeSwap(router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes; attacker receives output tokens
  - allowedSwapper[pool][attacker] was never consulted
```

The allowlist is fully bypassed. The attacker trades on a pool that was configured to exclude them, with direct loss of the curation guarantee and potential LP-value leakage if the pool was restricted for economic reasons (e.g., only KYC'd counterparties, only protocol-owned addresses).

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
