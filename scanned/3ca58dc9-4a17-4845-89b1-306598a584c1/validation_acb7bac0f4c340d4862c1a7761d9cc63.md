### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (correct) and `sender` is `msg.sender` of the `pool.swap()` call — which is the **router**, not the end user. When any user routes through `MetricOmmSimpleRouter`, the extension evaluates the router's address against the allowlist, not the individual user's address. If the router is allowlisted (required for any user to swap through it), every user on the network can bypass the per-user access control.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap() — the router, not the end user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`:

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

At this point `msg.sender` of `pool.swap()` is the **router contract address**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router_address]`.

This creates a binary outcome for pool admins:
- **Router not allowlisted**: No user can swap through the router, even allowlisted ones — core swap functionality is broken.
- **Router allowlisted**: Every user on the network can swap through the router, completely defeating per-user access control.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of which call `pool.swap()` from the router's address.

---

### Impact Explanation

A pool admin who deploys with `SwapAllowlistExtension` to create a restricted/private pool (e.g., institutional-only, KYC-gated, or counterparty-specific) cannot enforce per-user access control when users interact through `MetricOmmSimpleRouter`. Any non-allowlisted user can swap against the pool by routing through the router, exposing LPs to unintended counterparties and toxic flow. Since the pool's liquidity is priced by an external oracle, adversarial flow from non-allowlisted users can extract value from LPs who believed they were protected by the allowlist. This constitutes a direct loss of LP principal through unauthorized swap execution against a pool designed to be restricted.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing interface for swaps. Any user who wants to swap without implementing `IMetricOmmSwapCallback` themselves will use the router. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only calling the router's standard `exactInputSingle` function. Every non-allowlisted user who discovers the pool can exploit this.

---

### Recommendation

The extension must check the actual end user, not the intermediary router. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to populate `extensionData` honestly, which is acceptable since the router is a known periphery contract.

2. **Check `sender` against the allowlist but require direct pool interaction**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this by checking that `sender` is not a known router address. This is fragile as new routers can be deployed.

The cleanest fix is option 1: modify `SwapAllowlistExtension.beforeSwap` to decode the actual user from `extensionData` when `sender` is a known router, or require the router to always forward the originating user's address in a standardized `extensionData` field.

---

### Proof of Concept

**Setup**: Pool deployed with `SwapAllowlistExtension`. Pool admin calls `setAllowedToSwap(pool, router_address, true)` (required for any user to swap via router). Alice (`0xAlice`) is NOT individually allowlisted.

**Attack**:
1. Alice calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ..., extensionData: ""})`.
2. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", "")` — `msg.sender` = router.
3. Pool calls `_beforeSwap(router_address, ...)`.
4. Extension evaluates `allowedSwapper[pool][router_address]` → `true` → swap proceeds.
5. Alice successfully swaps against the restricted pool despite not being individually allowlisted.

**Code trace**: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
