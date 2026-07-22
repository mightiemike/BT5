### Title
`SwapAllowlistExtension.beforeSwap` gates on the router's address instead of the actual end-user, allowing any caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end-user. The extension therefore gates on the router's address rather than the real swapper. If the router is allowlisted (a natural admin action to let curated users access the router), every unprivileged user can bypass the per-user restriction and trade on the curated pool.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()`.
2. Router stores the real user in transient storage for the payment callback, then calls `pool.swap(recipient, ...)` — the pool's `msg.sender` is now the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and dispatches `IMetricOmmExtensions.beforeSwap(sender=router, ...)` to every configured extension.
5. `SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the **router**, so the check is `allowedSwapper[pool][router]`. The actual end-user's address is never consulted.

**Two concrete failure modes arise:**

| Scenario | Outcome |
|---|---|
| Pool admin allowlists the router (so curated users can use it) | Every unprivileged user can swap through the router — the per-user allowlist is fully bypassed |
| Pool admin allowlists specific user addresses only | Those users cannot use the router at all; they must call the pool directly — the supported periphery path is broken for allowlisted users |

Neither outcome matches the invariant that "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."

The real user address is available in the router's transient storage (`_getPayer()` / `_setNextCallbackContext`) but is never forwarded to the extension.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` for KYC/curation purposes is fully open to any caller who routes through `MetricOmmSimpleRouter` once the router is allowlisted. Non-allowlisted users can execute swaps against LP positions, extracting value at oracle-anchored prices. Because the pool's spread and notional fees are calibrated for a known counterparty set, unrestricted access directly exposes LP principal to adverse selection and value leakage that the allowlist was designed to prevent.

---

### Likelihood Explanation

**Medium.** The bypass requires the router to be allowlisted. A pool admin who wants curated users to access the router will naturally allowlist it, not realising this opens the pool to everyone. The misunderstanding is easy to make because the admin-facing API (`setAllowedToSwap(pool, router, true)`) gives no indication that it grants universal access. The router is a supported, documented periphery contract, so this is a realistic production configuration.

---

### Recommendation

Pass the real end-user through the swap path so the extension can gate on the correct actor. Two options:

1. **Preferred — forward the originating user as an extra field.** Add an `originator` field to the `beforeSwap` hook signature (or encode it in `extensionData`). The router sets `originator = msg.sender` before calling the pool; the pool forwards it to the extension. The extension checks `allowedSwapper[pool][originator]`.

2. **Simpler — check `sender` only when it is not a known router.** Maintain a factory-level registry of trusted routers; the extension falls back to checking the originator encoded in `extensionData` when `sender` is a registered router.

Either way, the extension must never treat a public intermediary contract as the authoritative identity for access control.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as before-swap hook
  admin calls: setAllowedToSwap(pool, router, true)   // intends to let curated users use the router
  admin calls: setAllowedToSwap(pool, alice, true)    // alice is the only intended swapper

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

Trace:
  router → pool.swap(recipient=bob, ...)          // pool.msg.sender = router
  pool   → _beforeSwap(sender=router, ...)
  pool   → extension.beforeSwap(sender=router, ...)
  extension checks: allowedSwapper[pool][router]  // = true  ← router is allowlisted
  → no revert; bob's swap executes on the curated pool

Result:
  bob, who is not on the allowlist, successfully swaps against LP positions.
  The per-user allowlist is completely bypassed via the supported router path.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
