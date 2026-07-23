### Title
`SwapAllowlistExtension` checks the router address instead of the original user, allowing any caller to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmPool.swap()` is called through `MetricOmmSimpleRouter`, the pool passes `msg.sender` (the router contract) as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not whether the **original user** is allowlisted. If the pool admin allowlists the router address, every user who routes through it bypasses the curated-pool restriction entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly — the pool's `msg.sender` is the **router**, not the original user: [4](#0-3) 

No original-user identity is forwarded to the pool. The extension therefore evaluates `allowedSwapper[pool][router_address]` instead of `allowedSwapper[pool][original_user]`.

**Two concrete failure modes arise:**

1. **Allowlist bypass (High):** The pool admin allowlists the `MetricOmmSimpleRouter` address — a natural action if the admin wants to permit router-mediated swaps. Because the extension checks the router, *any* user who calls the router passes the check, regardless of whether they are individually allowlisted. The curated-pool restriction is completely defeated.

2. **Broken functionality for allowlisted users (Medium):** If the admin does *not* allowlist the router, individually allowlisted users cannot use the router at all — the extension sees the router address and reverts with `NotAllowedToSwap`. Allowlisted users are forced to call `pool.swap()` directly, breaking the standard periphery flow.

The same mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router, and to intermediate hops in multi-hop paths where the router is `msg.sender` for every pool call: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` as a `beforeSwap` hook is intended to restrict trading to a curated set of addresses. The bypass allows non-allowlisted users to trade on such a pool through the router, violating the curation invariant. Depending on pool design, this can result in:

- Non-allowlisted users extracting value from LP positions on a pool that was meant to be restricted (direct loss of LP principal/fees).
- Regulatory or compliance pools (e.g., KYC-gated) being opened to arbitrary counterparties.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entry point for end users. Any pool admin who configures a swap allowlist and also permits router access (the expected operational pattern) triggers the bypass automatically. No special attacker capability is required beyond calling the public router.

---

### Recommendation

The extension must gate on the **original user**, not the intermediary. Two complementary fixes:

1. **Pass original-user identity through the router.** The router should forward `msg.sender` (the original user) to the pool via the `extensionData` field, and the extension should decode and check that address. This requires a convention between the router and the extension.

2. **Alternatively, gate on `recipient` instead of `sender` in the extension.** The `recipient` is the address that receives output tokens and is set by the original user. For single-hop swaps this is the user or their delegate. This is a weaker fix because `recipient` can be set to any address.

3. **Document clearly** that `SwapAllowlistExtension` is incompatible with router-mediated swaps in its current form, and that allowlisting the router address opens the pool to all router callers.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to allow router-mediated swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker (not individually allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: attacker, ...})
  - Router calls pool.swap(attacker, ...) — pool's msg.sender = router.
  - Pool calls _beforeSwap(router, attacker, ...).
  - Extension checks allowedSwapper[pool][router] == true → passes.
  - Swap executes; attacker receives output tokens.

Result:
  - attacker bypassed the swap allowlist.
  - allowedSwapper[pool][attacker] was never set to true.
```

Relevant code path: [6](#0-5) [7](#0-6) [8](#0-7)

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
