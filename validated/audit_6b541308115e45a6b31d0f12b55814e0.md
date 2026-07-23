### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual end user. Any user can therefore bypass a curated pool's swap allowlist by routing through the public router, because the extension sees the router address — not the real swapper — as the identity to check.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- router address when called via router
  recipient,
  ...
);
```

`_beforeSwap` forwards this value unchanged to the configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)   // sender = router address
  )
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether this `sender` (the router) is in the allowlist keyed by `msg.sender` (the pool):

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

For the router to be usable on an allowlisted pool at all, the pool admin must add the router to `allowedSwapper[pool][router] = true`. Once the router is allowlisted, **every user** — including those not on the allowlist — can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and the extension will pass, because it sees the allowlisted router address as the swapper, not the actual caller.

The same wrong-actor binding applies to multi-hop `exactInput` and `exactOutput` paths, where intermediate hops also call `pool.swap()` with the router as `msg.sender`.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers). The allowlist is completely bypassed for any user who routes through the public `MetricOmmSimpleRouter`. The non-allowlisted user receives pool output tokens and the pool's LP positions are exposed to unrestricted trading, directly violating the curation invariant the pool admin configured. This constitutes a broken core pool functionality and unauthorized access to restricted LP assets.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented and deployed by the protocol. Any user who discovers the allowlist can trivially route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-block setup are required — a single `exactInputSingle` call suffices. The trigger is fully unprivileged and reachable on every allowlisted pool that supports router-based swaps.

---

### Recommendation

The extension must check the actual end user, not the intermediary. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool, and the extension decodes and checks it. This requires a protocol-level convention.

2. **Check `sender` against the allowlist only when `sender` is not a known router, and require the router to forward the real user identity**: The cleanest fix is for the pool to expose a way for the router to attest the real caller, or for the extension to check `recipient` (which is the actual beneficiary) rather than `sender` when `sender` is a known intermediary.

The simplest correct fix is to have the router pass the real user address in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when present, falling back to `sender` only for direct pool calls.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for allowlisted users.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(router, ...)` → `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob receives output tokens despite never being allowlisted.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
