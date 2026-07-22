### Title
`SwapAllowlistExtension` checks the router address as the swapper identity, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. The extension therefore checks the router's address against the allowlist, not the actual trader's address. This creates a binary failure: either allowlisted users cannot swap via the router at all, or — if the pool admin allowlists the router to fix that — any unprivileged user can bypass the allowlist entirely by routing through the router.

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
         → pool.swap(params.recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → checks allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls `pool.swap` directly with no mechanism to forward the original user's address as `sender`: [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner explicitly passed by the caller), not `sender` (the adder contract): [5](#0-4) 

This asymmetry means the deposit allowlist is correctly actor-bound while the swap allowlist is not.

### Impact Explanation

Two mutually exclusive failure modes arise for any pool that configures `SwapAllowlistExtension`:

1. **Allowlisted users locked out of the router.** If the pool admin allowlists individual user addresses (the intended design), every router-mediated swap reverts with `NotAllowedToSwap` because the router address is not on the list. Allowlisted users must call `pool.swap` directly, which requires implementing `IMetricOmmSwapCallback` — not possible for EOAs. Core swap functionality is broken for the intended user set.

2. **Full allowlist bypass.** If the pool admin allowlists the router address to restore router access, every user — including those the allowlist was meant to exclude — can bypass the gate by routing through `MetricOmmSimpleRouter`. The allowlist provides zero protection.

Both outcomes are fund-impacting: the first breaks the usable swap flow for legitimate users; the second nullifies a curated-pool access control that may be protecting LP assets from unauthorized counterparties.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint for EOA users. Any pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set (e.g., KYC'd counterparties, institutional LPs) will encounter this issue the moment a user attempts a router swap. No special preconditions, privileged access, or malicious setup is required — a standard `exactInputSingle` call is sufficient.

### Recommendation

The `beforeSwap` hook should check the **end user** identity, not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension`:** check `recipient` (the address receiving swap output) or require the pool to pass the original user address through `extensionData`. Alternatively, mirror the deposit allowlist pattern and check a caller-supplied owner field rather than the pool's `msg.sender`.

2. **In `MetricOmmSimpleRouter`:** encode the original `msg.sender` into `extensionData` so extensions can recover the true initiator, and document that allowlist extensions must read from `extensionData` rather than the `sender` argument for router-mediated flows.

### Proof of Concept

```
Pool configured with SwapAllowlistExtension.
Pool admin calls: setAllowedToSwap(pool, alice, true)
Pool admin does NOT allowlist the router.

Alice (EOA) calls:
  router.exactInputSingle({pool: pool, recipient: alice, ...})
    → pool.swap(alice, ...) with msg.sender = router
    → beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == false → revert NotAllowedToSwap ✗

Alice is allowlisted but cannot swap via the router.

---

Pool admin now calls: setAllowedToSwap(pool, router, true)  // to fix Alice

Bob (not allowlisted) calls:
  router.exactInputSingle({pool: pool, recipient: bob, ...})
    → pool.swap(bob, ...) with msg.sender = router
    → beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true → passes ✓

Bob bypasses the allowlist entirely.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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
