### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is the pool's `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted (required for any router-mediated swap to work), every user on the internet can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

The pool therefore sees `msg.sender = router`. The extension checks `allowedSwapper[pool][router]`. The actual end user's address is never examined.

The same pattern applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`: [5](#0-4) 

The pool admin faces an inescapable dilemma:

- **Allowlist the router** → every user on the internet can swap through the router, defeating the allowlist entirely.
- **Do not allowlist the router** → no user can ever use the router on this pool, breaking the supported periphery path.

There is no configuration that simultaneously enables router-mediated swaps and enforces per-user access control.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to specific addresses (e.g., KYC-verified counterparties, whitelisted protocols, or specific market makers). Once the router is allowlisted to enable normal periphery usage, any unprivileged user can call `exactInputSingle` or `exactInput` and execute swaps on the curated pool. This allows:

- Unauthorized arbitrageurs to extract value from LP positions at oracle-derived prices.
- Compliance/curation policies to be silently bypassed.
- LP principal loss through trades the pool admin explicitly intended to block.

This is a direct loss of LP assets and a broken core pool functionality (the allowlist guard fails open for all router-mediated swaps).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users. Any pool that enables the router for allowlisted users automatically exposes the bypass to all users. The attacker needs only to call a public router function with no special privileges, no malicious setup, and no non-standard tokens. Likelihood is high whenever a pool uses `SwapAllowlistExtension` alongside the router.

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary. Two approaches:

1. **Pass the original user through the router**: The router stores `msg.sender` in transient storage (already done for the payer context). The pool or extension could read a "true sender" from a trusted router context. However, this couples the extension to a specific router implementation.

2. **Check `recipient` instead of `sender`** (partial): For exact-input swaps the recipient is the user, but for exact-output or intermediate hops it may be `address(this)`.

3. **Preferred — require direct pool interaction for allowlisted pools**: Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router; users on such pools must call `pool.swap()` directly. The extension should expose a clear warning or the factory should enforce this constraint.

4. **Alternatively**: Redesign `SwapAllowlistExtension` to accept an `extensionData`-encoded user address that the router signs or forwards, and verify it against a trusted forwarder registry.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is blocked

Attack:
  - alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) → msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true → passes
  - Alice's swap executes on the curated pool despite not being allowlisted.

Result:
  - Alice swaps successfully.
  - isAllowedToSwap(pool, alice) returns false, but alice traded anyway.
  - LP funds are exposed to any user via the public router.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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
```
