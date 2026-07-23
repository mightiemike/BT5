### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any Unprivileged User to Bypass the Swap Allowlist on Curated Pools — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking the `sender` argument forwarded by the pool. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. For router-mediated swaps to function on an allowlisted pool, the pool admin must allowlist the router. Once the router is allowlisted, every public user can bypass the allowlist by routing through it, rendering the curation control ineffective.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the value the pool passes as the first argument to `beforeSwap`. The pool populates `sender` from its own `msg.sender` — i.e., whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInput` calls `pool.swap()` directly without forwarding the end user's address:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        zeroForOne,
        amount,
        MetricOmmSwapPath.openLimit(zeroForOne),
        "",
        params.extensionDatas[i]
    );
``` [2](#0-1) 

The pool therefore sees the router as `msg.sender` and forwards the router's address as `sender` to `_beforeSwap`: [3](#0-2) 

The extension then evaluates `allowedSwapper[pool][router]`. For any router-mediated swap to succeed on an allowlisted pool, the admin must add the router to the allowlist. The moment the router is allowlisted, the check degenerates: every caller of the router passes because the extension only sees the router's address, never the actual user's address.

The same structural issue applies to `exactOutputSingle` and `exactOutput`: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

Any user who is explicitly excluded from the allowlist can bypass the curation gate by calling any of the router's public swap entry points. The allowlist is the primary access-control mechanism for curated pools; its silent failure opens the pool to adversarial counterparties. Depending on pool design, this enables unauthorized price-taking, sandwich attacks against LPs, or violation of regulatory/contractual restrictions that the allowlist was meant to enforce — all constituting direct LP principal risk on a broken core pool flow.

---

### Likelihood Explanation

The trigger is fully unprivileged: any EOA can call the public router. The only precondition is that the pool admin has allowlisted the router, which is a necessary operational step for the router to be usable at all on the pool. Any pool that intends to support both router-mediated swaps and allowlist enforcement is therefore vulnerable by construction.

---

### Recommendation

The router must forward the originating user's address to the pool so the extension can gate the economically relevant actor. One approach: add a `swapper` field to the swap parameters and have the pool pass it (rather than `msg.sender`) as `sender` to extensions. Alternatively, the extension can read a trusted forwarder context set by the router in transient storage (mirroring the existing `_setNextCallbackContext` pattern used for callback payment) and verify the real caller from there.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` active.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — required for any router swap to work.
3. Pool admin does **not** allowlist Alice (`allowedSwapper[pool][Alice] == false`).
4. Alice calls `MetricOmmSimpleRouter.exactInput(...)`.
5. The router calls `pool.swap(recipient, zeroForOne, amount, openLimit, "", extensionData)`.
6. The pool calls `_beforeSwap(router, ...)` — `sender = router`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → no revert.
8. Alice's swap executes on the curated pool despite being explicitly excluded from the allowlist. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-188)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = _getExactOutputAmountIn();
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
