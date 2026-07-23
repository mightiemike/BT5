### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter: Router Address Replaces User Identity in Allowlist Check - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks whether the **router** is allowlisted rather than the actual end user. Any pool admin who allowlists the router to enable router-based swaps simultaneously opens the allowlist to every user on the network.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The router never forwards the originating user's address to the extension. The `extensionData` bytes are passed through, but `SwapAllowlistExtension` does not read them for identity purposes. [5](#0-4) 

**Result**: the extension evaluates `allowedSwapper[pool][router_address]`. If the pool admin allowlists the router (required for any router-mediated swap to succeed), the check passes for every caller regardless of their individual allowlist status.

### Impact Explanation

A curated pool's per-user swap allowlist is completely nullified for any user who routes through the public `MetricOmmSimpleRouter`. Non-allowlisted users execute swaps on pools designed to restrict trading to specific counterparties (e.g., KYC-gated, institutional, or compliance-restricted pools). LP funds in those pools are exposed to unrestricted trading, violating the pool's intended access policy and potentially causing direct loss of LP value through unwanted price impact or arbitrage from disallowed actors.

### Likelihood Explanation

The router is a standard, publicly deployed periphery contract. Any pool that wants to support router-based swaps for its allowlisted users must allowlist the router address. Once the router is allowlisted, the bypass is trivially available to every address on the network with zero additional privilege. No special setup, flash loan, or privileged role is required.

### Recommendation

The `SwapAllowlistExtension` must gate on the **originating user**, not the immediate pool caller. Two sound approaches:

1. **Pass originator through `extensionData`**: The router encodes `msg.sender` into the `extensionData` for each hop, and the extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to be non-allowlistable**: Treat the router as a transparent forwarder and require pools using `SwapAllowlistExtension` to never allowlist the router address. Document that router-based swaps are incompatible with per-user swap allowlists.

The cleanest long-term fix is option 1: the router encodes `abi.encode(msg.sender)` as the first element of `extensionData` for each pool hop, and `SwapAllowlistExtension` decodes and checks that address when `sender` is a known router.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true      // alice is the only allowed user
  allowedSwapper[pool][router] = true     // admin must set this for alice to use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., extensionData: ""})

  Execution trace:
    router.exactInputSingle()
      -> pool.swap(msg.sender=router, ...)
        -> _beforeSwap(sender=router, ...)
          -> SwapAllowlistExtension.beforeSwap(sender=router)
             check: allowedSwapper[pool][router] == true  ✓  (passes)
        -> swap executes for bob
```

Bob's swap succeeds despite never being allowlisted. The extension saw `sender = router` and found it allowlisted, never inspecting bob's address. [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

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
