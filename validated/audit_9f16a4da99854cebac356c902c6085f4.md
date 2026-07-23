### Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` inside the pool is the router, so `sender` forwarded to the extension is the router address — not the end user. A pool admin who allowlists the router (the natural step to let allowlisted users trade through the standard periphery) inadvertently opens the gate to every user, because the extension sees only the router and approves the call unconditionally.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding its own `msg.sender` as the `sender` argument: [1](#0-0) 

`_beforeSwap` encodes that value and dispatches it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The end user's address is stored only in transient callback context (`_setNextCallbackContext`) for payment settlement; it is never forwarded to the pool or to any extension. The extension therefore has no way to distinguish which end user initiated the router call.

**Consequence:** A pool admin who wants allowlisted users to be able to trade through the router must add the router to `allowedSwapper`. The moment the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every call that arrives through the router, regardless of who the actual end user is. Any address can call `exactInputSingle` and swap against the pool.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade against a pool. Bypassing it lets unauthorized users execute swaps that the pool admin explicitly intended to block. Depending on the pool's purpose (institutional-only liquidity, KYC-gated market making, or pools with favorable oracle pricing reserved for specific counterparties), this allows arbitrary users to drain LP value through unrestricted swaps. This directly satisfies the "broken core pool functionality causing loss of funds" and "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact criteria.

---

### Likelihood Explanation

The trigger requires only two ordinary, expected actions:

1. A pool admin deploys a pool with `SwapAllowlistExtension` on `BEFORE_SWAP_ORDER`.
2. The pool admin calls `setAllowedToSwap(pool, router, true)` — the natural step to let allowlisted users trade through the standard periphery.

After step 2, any user can call `MetricOmmSimpleRouter.exactInputSingle()` and bypass the allowlist. No special privilege, flash loan, or oracle manipulation is needed. The admin action is not malicious; it is the expected operational step and the bug is that it has an undocumented, fund-impacting side effect.

---

### Recommendation

The extension must verify the actual end user, not the intermediary. Two sound approaches:

1. **Pass the real swapper in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. The pool admin allowlists individual users, not the router.
2. **Dedicated router allowlist**: Maintain a separate `allowedRouter` set. When `sender` is an allowlisted router, decode the real swapper from `extensionData` and check that address against `allowedSwapper`.

Either way, the router address must never be the identity that the allowlist gates on.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension on BEFORE_SWAP_ORDER.
2. Pool admin calls setAllowedToSwap(pool, alice, true)      // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)     // to let alice use the router

Attack
──────
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: bob})
   Inside pool.swap():
       msg.sender = router
       _beforeSwap(router, ...)
   Inside SwapAllowlistExtension.beforeSwap(sender=router, ...):
       allowedSwapper[pool][router] == true  →  check passes
5. Bob's swap executes and settles against LP funds.

Result: Bob, who was never allowlisted, successfully swaps against the pool,
        bypassing the SwapAllowlistExtension entirely.
``` [3](#0-2) [4](#0-3) [1](#0-0)

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
