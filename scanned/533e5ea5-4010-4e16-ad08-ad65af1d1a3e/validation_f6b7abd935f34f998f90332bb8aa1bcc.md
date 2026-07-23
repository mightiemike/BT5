### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swap access on a per-pool basis by checking whether the swapper is allowlisted. However, the hook checks `sender`, which is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the router address, not the actual end-user. If the router is allowlisted — a natural production setup for any pool that supports router-mediated swaps — the allowlist is completely bypassed for every user who routes through it.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the router contract, not the end-user:

```solidity
// MetricOmmSimpleRouter.sol
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

So the allowlist check becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router — a reasonable step for any pool that wants to support router-mediated swaps — every user, regardless of their own allowlist status, can bypass the guard by routing through `MetricOmmSimpleRouter`. There is no mechanism in the extension to recover the original end-user identity from the call.

This is structurally identical to the external bug: the guard is designed to check one entity (the actual swapper) but structurally checks a different entity (the router), making the protection always pass for router-mediated swaps when the router is allowlisted.

---

### Impact Explanation

Any user who is explicitly excluded from the allowlist can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant). The pool receives the swap and settles it normally. The allowlist — the sole access-control mechanism for swap gating on curated pools — provides no protection against router-mediated swaps. This constitutes a broken core pool functionality: the configured protection fails open for the primary supported user entry point.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard periphery entry point for swaps. Any pool admin who deploys a pool with `SwapAllowlistExtension` and also wants users to be able to use the router must allowlist the router. This is the expected production configuration. Once the router is allowlisted, the bypass is available to every user unconditionally, requiring no special privileges, no flash loans, and no multi-transaction setup.

---

### Recommendation

The extension must gate the actual end-user, not the intermediary. Two viable approaches:

1. **Forward the original caller in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` in addition to `sender`**: For single-hop swaps the recipient is often the end-user. However, this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Separate router-aware allowlist**: Introduce a second mapping `allowedSwapperViaRouter[pool][user]` and have the router pass the original caller in `extensionData` for the extension to decode and verify.

Additionally, the `setAllowedToSwap` admin function should document that allowlisting the router grants unrestricted access to all users, so admins are not misled into thinking they can combine router allowlisting with per-user restrictions.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowAllSwappers(pool, false)` — allowlist mode is active.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so that normal users can trade.
4. Pool admin does **not** call `setAllowedToSwap(pool, userA, true)` — `userA` is explicitly excluded.
5. `userA` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
6. The router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` with `msg.sender = router`.
7. `MetricOmmPool._beforeSwap(sender=router, ...)` is dispatched to `SwapAllowlistExtension.beforeSwap`.
8. The extension evaluates `allowedSwapper[pool][router] == true` → no revert.
9. `userA`'s swap executes successfully despite being excluded from the allowlist.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
