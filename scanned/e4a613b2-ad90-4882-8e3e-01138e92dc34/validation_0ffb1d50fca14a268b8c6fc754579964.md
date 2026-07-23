### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool level is the router contract, not the end user. If the pool admin allowlists the router (a natural action to enable router-based swaps), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension` is designed to restrict which addresses may swap on a curated pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension's caller), and `sender` is the first argument forwarded by the pool. The pool sets that argument to its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // <-- pool's msg.sender becomes `sender` in the extension
    recipient, zeroForOne, amountSpecified, priceLimitX64,
    packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  lines 72-80
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

The router is `msg.sender` of `pool.swap()`, so the extension receives `sender = router`. The check becomes:

```
allowedSwapper[pool][router]
```

not

```
allowedSwapper[pool][user]
```

A pool admin who wants allowlisted users to be able to use the official router will call `setAllowedToSwap(pool, router, true)`. From that moment, every address — including those never individually allowlisted — can call `router.exactInputSingle(...)` and the extension passes them through. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

**High.** The `SwapAllowlistExtension` is the sole on-chain mechanism for curated pools to restrict who may trade. Once the router is allowlisted (a routine admin action), the allowlist provides zero protection: any address can swap by routing through `MetricOmmSimpleRouter`. This is a direct policy bypass on curated pools, enabling unauthorized users to extract liquidity at oracle-anchored prices from pools that were intended to be restricted.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected action — without it, even individually allowlisted users cannot use the router. Any pool that wants to support both direct and router-based swaps for its allowlisted users will trigger the vulnerability. The router is a first-party, factory-validated contract, so allowlisting it is not a suspicious or adversarial act.

---

### Recommendation

The extension must check the actual economic actor, not the intermediary. Two options:

1. **Check `msg.sender` of the router call.** The router already stores the real payer in transient storage (`_setNextCallbackContext(..., msg.sender, ...)`). Expose it as a standard field (e.g., pass it through `extensionData` or a dedicated hook argument) so the extension can verify the originating user.

2. **Require direct pool calls for allowlisted pools.** Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router; instead, allowlisted users must call `pool.swap()` directly. This is fragile and breaks UX.

Option 1 is the correct fix. The `beforeSwap` hook should receive the originating user address (the payer/initiator), not just the immediate `msg.sender` of the pool call.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // enable router for allowlisted users
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice is individually allowlisted
  bob is NOT individually allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(...)          // msg.sender = router
    → pool calls _beforeSwap(router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob
```

Bob successfully swaps on a pool that was supposed to deny him, because the allowlist check resolves to the router's allowance, not Bob's. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
