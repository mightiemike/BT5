### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the actual swapper, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. A pool admin who allowlists the router (the only way to permit allowlisted users to use the router) inadvertently opens the gate to every user, defeating the curated-pool invariant.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` as its own `msg.sender`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][user_address]`. The original user's identity is never consulted.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the `beforeSwap` guard passes for **every** caller who routes through the router, regardless of whether that caller is on the allowlist. Non-allowlisted users can freely swap on the curated pool, draining LP funds at oracle prices. This is a direct loss of the curation guarantee the pool admin configured. [5](#0-4) 

---

### Likelihood Explanation

The pool admin must allowlist the router for the bypass to be reachable. However, this is the natural and expected action: without allowlisting the router, no allowlisted user can use the router either, making the router unusable on curated pools. The protocol's own audit targets explicitly flag this path:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [6](#0-5) 

Once the router is allowlisted (a single admin transaction), the bypass is trivially reachable by any unprivileged user with no further preconditions.

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** `MetricOmmSimpleRouter` already knows `msg.sender` (the real user). It could encode the real user in `extensionData` and the extension could decode and verify it — but this is forgeable unless the pool trusts the router.

2. **Gate on `sender` only when the caller is a trusted router; otherwise gate on `msg.sender` of the pool call.** A cleaner approach: the extension checks whether `sender` is a known trusted router; if so, it decodes the real user from `extensionData`; otherwise it uses `sender` directly.

3. **Simplest safe fix:** Do not allowlist the router address in the extension. Instead, document that router-mediated swaps on allowlisted pools are unsupported, and require allowlisted users to call `pool.swap()` directly. This eliminates the bypass at the cost of router compatibility.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order = extension 1)
  alice = allowlisted user  (allowedSwapper[pool][alice] = true)
  bob   = non-allowlisted user
  admin allowlists the router: allowedSwapper[pool][router] = true
    (necessary so alice can use the router)

Attack (bob):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender in pool = router
  3. pool._beforeSwap(sender=router, ...) → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  4. Check: allowedSwapper[pool][router] == true  → PASSES
  5. Swap executes; bob receives output tokens at oracle price
  6. LP funds are drained by an actor the pool admin never intended to allow
```

The allowlist guard is silently bypassed. The pool admin cannot distinguish between alice and bob at the extension level once the router is allowlisted. [3](#0-2) [7](#0-6) [8](#0-7)

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

**File:** generate_scanned_questions.py (L659-663)
```python
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
