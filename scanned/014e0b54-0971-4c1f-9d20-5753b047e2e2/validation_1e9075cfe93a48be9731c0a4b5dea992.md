### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the originating user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

The `SwapAllowlistExtension` gates swaps by checking the `sender` argument passed to `beforeSwap`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the original user's address. A pool admin who allowlists the router (a natural step to enable router-based swaps for their curated users) inadvertently opens the pool to every user, fully bypassing the intended access-control gate.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the contract that called the extension), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's own `swap()` call.

Tracing the call path through `MetricOmmSimpleRouter.exactInputSingle`:

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the original user
```

The extension therefore checks whether the **router** is allowlisted, not whether the originating user is allowlisted.

This creates two mutually exclusive failure modes:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users **cannot** use the router; their swaps revert with `NotAllowedToSwap` even though they are individually permitted. Core swap flow is broken for the supported periphery path. |
| Yes | **Any** user can bypass the allowlist by routing through `MetricOmmSimpleRouter`; the allowlist is completely ineffective for router-mediated swaps. |

The second case is the critical one. A pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. Doing so silently removes all per-user gating for every router-mediated swap.

---

### Impact Explanation

A curated pool with `SwapAllowlistExtension` configured is designed to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses). Once the router is allowlisted, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and trade against the pool without restriction. The allowlist invariant — that only approved addresses may swap — is broken for all router-mediated flows, which is the primary supported public entrypoint for end users.

---

### Likelihood Explanation

The trigger is a pool admin allowlisting the router, which is the natural and expected configuration step for any curated pool that is meant to be accessible through the protocol's own periphery. There is no warning in the extension or the router that this combination defeats per-user gating. The pool admin acts in good faith; the code silently fails open.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **originating user**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Extension-data forwarding**: The router encodes `msg.sender` (the originating user) into `extensionData` for each swap hop; the extension decodes and checks that address. This requires a convention between the router and the extension.
2. **Separate `originalSender` field**: The pool or router exposes the originating user through a transient-storage slot (similar to how the router already stores the payer in `T_SLOT_PAY_PAYER`), and the extension reads it.

Either way, the extension must not treat the router as the actor being gated.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` attached to `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — `alice` is the only permitted swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to let `alice` use the router.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — `msg.sender` at the pool = router.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `bob`'s swap executes successfully against the curated pool, bypassing the allowlist entirely.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
