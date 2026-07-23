### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. If the pool admin allowlists the router to enable router-mediated swaps for their allowlisted users, every user — including non-allowlisted ones — can bypass the per-user restriction by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension caller), and `sender` is the first argument forwarded by the pool — the direct caller of `pool.swap()`.

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap() = router when routed
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` then encodes this as the `sender` argument to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with itself as `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So the allowlist check resolves to `allowedSwapper[pool][router]`. If the pool admin allowlists the router address (a natural action to enable router-mediated swaps for their allowlisted users), the check passes for **every** user who routes through the router, regardless of whether they are individually allowlisted.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position beneficiary), not `sender` (the liquidity adder contract), so the deposit allowlist is not analogously broken.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional traders, or protocol-controlled addresses) can be fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`, provided the router is allowlisted. The non-allowlisted user executes swaps against LP assets at oracle-derived prices, draining token reserves that the pool admin intended to protect from unrestricted access. This breaks the core invariant that the allowlist gates the economically relevant actor.

---

### Likelihood Explanation

The trigger is a pool admin allowlisting the router — a reasonable and expected configuration step for any pool that wants to support the standard periphery swap flow. The admin's intent is to allow their allowlisted users to use the router; the unintended consequence is that all users gain access. No malicious setup is required; the bypass is reachable by any user who calls the public router against a pool where the router is allowlisted.

---

### Recommendation

The `SwapAllowlistExtension` must gate the end user, not the intermediary router. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and verifies it. This requires the extension to trust that the router correctly reports the originator.

2. **Check `sender` only for direct pool calls; require the router to be non-allowlisted**: Document that the allowlist is incompatible with router-mediated swaps and that allowlisted users must call `pool.swap()` directly. This is a usability limitation but avoids the bypass.

The cleanest fix is option 1, with the router explicitly encoding the originating user in `extensionData` and the extension verifying it when `sender` is a known router address.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: extension.setAllowedToSwap(pool, alice, true)
  pool admin calls: extension.setAllowedToSwap(pool, router, true)
    ↑ intended to let alice use the router; inadvertently opens pool to all

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: weth, ...})

  router calls:
    pool.swap(recipient, zeroForOne, amount, ...)
    ↳ msg.sender = router

  pool calls extension:
    beforeSwap(sender=router, ...)
    ↳ allowedSwapper[pool][router] == true  ← passes

  bob's swap executes against LP assets — allowlist bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
