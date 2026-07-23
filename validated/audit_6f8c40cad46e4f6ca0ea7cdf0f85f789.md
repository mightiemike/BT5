### Title
`SwapAllowlistExtension` Gates the Router Address, Not the Actual Swapper — Any User Bypasses the Allowlist When the Router Is Allowlisted - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the user. If the pool admin allowlists the router (the natural action to let allowlisted users access the router), every unpermissioned address can bypass the gate by routing through the same public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

Consequently, the extension never sees the real user — it sees the router address. A pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper`. The moment the router is allowlisted, every address on the network can call `router.exactInputSingle(...)` and the extension passes, because `allowedSwapper[pool][router]` is `true` for all callers.

### Impact Explanation

Any address that is not on the allowlist can execute swaps on a restricted pool by routing through `MetricOmmSimpleRouter`. The pool's access-control invariant — "only approved swappers may trade" — is completely broken. Depending on the pool's purpose (KYC-gated, institutional, regulatory-restricted), this allows unauthorized parties to extract value at oracle-anchored prices, drain one-sided liquidity, or violate compliance requirements. The swap itself settles normally, so the pool suffers real token outflows to unpermissioned counterparties.

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is the natural and expected action when the pool admin wants allowlisted users to enjoy the router's UX (slippage protection, multi-hop, deadline checks). There is no in-protocol warning that doing so opens the gate to all router callers. The audit hint for this target explicitly flags that "public users may enter through the router" and that "the hook must gate the same actor the pool designers thought they were allowlisting," confirming this is a realistic operational mistake.

### Recommendation

The extension must recover the original user identity rather than trusting the `sender` argument, which is the immediate pool caller. Two sound approaches:

1. **Router forwards the real payer**: Add a `swapper` field to the router's transient callback context (already used for `payer` and `tokenIn`) and expose it via a view function. The extension reads `IMetricOmmSimpleRouter(sender).currentSwapper()` when `sender` is a known router, falling back to `sender` for direct calls.

2. **Extension checks `tx.origin` as a secondary gate**: Acceptable only if the pool is not intended to be called from other contracts; otherwise it introduces its own bypass surface.

The cleanest fix is approach (1): the router already stores per-call transient context; adding the original `msg.sender` there and having the extension query it when the immediate caller is a recognized router preserves the allowlist semantics for both direct and router-mediated swaps.

### Proof of Concept

```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router
4. bob is NOT allowlisted.

Attack
──────
5. bob calls router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...) with msg.sender = router
   → pool calls _beforeSwap(router, ...)
   → extension checks allowedSwapper[pool][router] → true
   → swap executes; bob receives tokens from the restricted pool.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
