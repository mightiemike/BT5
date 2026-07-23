### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. If the pool admin allowlists the router (the only way to enable router-mediated swaps on a curated pool), every unpermissioned user can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the calling pool:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool populates `sender` with its own `msg.sender` — the direct caller of `pool.swap()`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified, priceLimitX64, ...)
    )
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the **router contract**, not the end user:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The router passes no information about the originating user. The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist entry — rather than the actual user's entry. The router does not forward the end user's identity in any form.

**The trap for pool admins:** A curated pool that wants to support router-mediated swaps for its allowlisted users must add the router to `allowedSwapper`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller, regardless of who the actual user is. The allowlist is silently rendered inoperative for all router-mediated swaps.

---

### Impact Explanation

Any user — including those explicitly excluded from the allowlist — can swap in a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist invariant ("only permitted addresses may trade") is broken for the entire router-mediated path. Curated pools designed for institutional, KYC-gated, or otherwise restricted participants are fully open to arbitrary traders the moment the router is allowlisted.

---

### Likelihood Explanation

The scenario is reachable by any unpermissioned user without any privileged action on their part. The only prerequisite is that the pool admin has allowlisted the router — a step the admin must take if they want any of their permitted users to trade via the router. The admin has no way to allowlist the router for specific users only; the allowlist entry is binary (router allowed or not). The bypass is therefore an unavoidable consequence of enabling router support on a curated pool.

---

### Recommendation

The extension must gate on the **economic actor**, not the **immediate caller**. Two sound approaches:

1. **Caller-supplied identity in `extensionData`:** Have the router encode the originating user's address in `extensionData` and have `SwapAllowlistExtension` decode and verify it. The pool's `onlyPool` guard already ensures only a registered pool can invoke the extension, so the pool is trusted to forward the data faithfully.

2. **Router-level enforcement:** Add a complementary allowlist check inside `MetricOmmSimpleRouter` before calling `pool.swap()`, so the router itself rejects non-permitted callers. This does not fix the extension's wrong-actor binding but adds a defense-in-depth layer.

Option 1 is preferred because it keeps the enforcement inside the extension and does not rely on every periphery contract implementing its own copy of the check.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured. Pool admin intends only `userA` to be able to swap.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — `userA` is allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so that `userA` can use the router.
4. `userB` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `userB`'s swap executes successfully, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/libraries/CallExtension.sol (L8-32)
```text
  function callExtension(address extension, bytes memory data) internal {
    (bool success, bytes memory result) = extension.call(data);
    if (!success) {
      if (result.length > 0) {
        assembly ("memory-safe") {
          revert(add(result, 32), mload(result))
        }
      }
      revert ExtensionCallFailed();
    }
    if (result.length < 32) {
      revert InvalidExtensionResponse();
    }
    bytes4 returnedSelector;
    assembly ("memory-safe") {
      returnedSelector := mload(add(result, 32))
    }
    bytes4 expectedSelector;
    assembly ("memory-safe") {
      expectedSelector := mload(add(data, 32))
    }
    if (returnedSelector != expectedSelector) {
      revert InvalidExtensionResponse();
    }
  }
```
