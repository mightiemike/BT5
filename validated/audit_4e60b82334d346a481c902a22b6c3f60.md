### Title
SwapAllowlistExtension gates the router address instead of the real user, allowing any unprivileged caller to bypass the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router (a natural operational step so that legitimate users can use the router), every unprivileged address can bypass the allowlist by calling the router instead of the pool directly.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient, zeroForOne, amountSpecified, priceLimitX64,
    packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact `sender` value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The pool receives `msg.sender = router`, so `sender = router` is what the extension sees. The actual end user's address is stored only in the transient callback context (for payment purposes) and is never surfaced to the extension.

**Attack path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Admin allowlists the router (`allowedSwapper[pool][router] = true`) so that legitimate users can use the router.
3. A non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool.
4. The pool calls `_beforeSwap(sender=router, ...)`.
5. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The attacker successfully swaps against a pool they were explicitly barred from.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with `msg.sender = router`.

### Impact Explanation

The swap allowlist is the primary access-control mechanism for restricting who may trade against a pool. Bypassing it allows unauthorized parties to:
- Execute swaps that drain LP token balances from a restricted pool.
- Trade against pools intended for whitelisted counterparties only (e.g., institutional or KYC-gated pools), causing direct loss of LP principal through adverse selection or unauthorized price impact.

This is a broken core pool functionality with direct loss of LP assets above contest thresholds.

### Likelihood Explanation

The bypass requires only that the router is allowlisted for the pool — a natural and expected operational step, since without it no legitimate user can use the router on that pool. Any attacker who observes the allowlist configuration on-chain can immediately exploit it by calling the router instead of the pool directly. No special privileges, flash loans, or timing are required.

### Recommendation

The extension must check the real end user, not the immediate `pool.swap()` caller. Two complementary fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes and checks it. This requires a protocol-level convention.

2. **Check `sender` and fall back to a trusted-forwarder registry**: The extension maintains a mapping of trusted routers; when `sender` is a trusted router, it reads the actual user from a standardized field in `extensionData`.

3. **Simplest safe fix**: Remove the router from the allowlist and require users to call `pool.swap()` directly when the allowlist is active. Document that the router is incompatible with `SwapAllowlistExtension`.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER.
// Admin has allowlisted the router (allowedSwapper[pool][router] = true)
// so that legitimate users can use it.
// Attacker (not in allowlist) calls the router:

IMetricOmmSimpleRouter.ExactInputSingleParams memory params = IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(restrictedPool),
    tokenIn:         address(token0),
    recipient:       attacker,
    deadline:        block.timestamp + 1,
    amountIn:        1e18,
    amountOutMinimum: 0,
    zeroForOne:      true,
    priceLimitX64:   0,
    extensionData:   ""
});

// pool.swap() sees msg.sender = router → extension checks allowedSwapper[pool][router] = true → passes
// Attacker bypasses the allowlist and executes the swap.
router.exactInputSingle(params);
```

The pool's `_beforeSwap` receives `sender = address(router)`, the extension approves it, and the attacker's swap settles normally despite never being allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
