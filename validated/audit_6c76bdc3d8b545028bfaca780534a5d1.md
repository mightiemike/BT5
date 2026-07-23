### Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router address (the natural step to let allowlisted users reach the pool through the router), every user — including those explicitly not on the allowlist — can bypass the guard by routing through the same public router.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` identity check:** [1](#0-0) 

The extension checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool.

**Pool passes `msg.sender` of `pool.swap()` as `sender`:** [2](#0-1) [3](#0-2) 

So `sender` = `msg.sender` of `pool.swap()`.

**Router calls `pool.swap()` directly, making itself `msg.sender`:** [4](#0-3) 

When a user calls `router.exactInputSingle(...)`, the router calls `pool.swap(...)` with `msg.sender = router`. The pool then calls `extension.beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router]` — the router's address, not the actual user's address.

**Consequence:** The pool admin cannot simultaneously:
1. Allow allowlisted users to reach the pool through the router (requires allowlisting the router address).
2. Block non-allowlisted users from using the same router.

Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router, regardless of who the actual end user is.

---

### Impact Explanation

Any user who is explicitly excluded from the swap allowlist can bypass the guard by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) targeting the restricted pool. The router becomes the checked identity, and since it is allowlisted, the `NotAllowedToSwap` revert is never triggered. Unauthorized users can then execute swaps against a pool whose admin intended to restrict access to specific counterparties, extracting value (arbitrage, price impact) from LP positions in a pool that was designed to be closed to them.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is the natural and expected action for any pool that wants its allowlisted users to interact through the standard periphery. The router is a public, permissionless contract; any user can call it. The likelihood that a pool admin allowlists the router while also intending to restrict individual users is high, because the two goals appear compatible but are not.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end user, not on the intermediary contract. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to be excluded from the allowlist:** Document that the router must never be allowlisted and that allowlisted users must call the pool directly. This is operationally fragile and error-prone.

The cleanest fix is approach 1, with the router always appending the originating user address to `extensionData` so the extension can verify the real actor.

---

### Proof of Concept

**Setup:**
- Pool is deployed with `SwapAllowlistExtension` in the `beforeSwap` hook order.
- Pool admin calls `setAllowedToSwap(pool, Alice, true)` — Alice is allowlisted.
- Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.

**Attack:**
1. Bob (not allowlisted) calls `router.exactInputSingle({pool: restrictedPool, ...})`.
2. Router calls `restrictedPool.swap(recipient, ...)` — `msg.sender = router`.
3. Pool calls `extension.beforeSwap(router, recipient, ...)`.
4. Extension evaluates: `allowedSwapper[pool][router] == true` → passes.
5. Bob's swap executes successfully against the restricted pool.

**Direct call (control):**
1. Bob calls `restrictedPool.swap(...)` directly — `msg.sender = Bob`.
2. Extension evaluates: `allowedSwapper[pool][Bob] == false` → `NotAllowedToSwap` revert.

The allowlist is enforced only for direct pool calls, not for router-mediated calls, making the guard ineffective for any pool that allowlists the router. [1](#0-0) [5](#0-4) [6](#0-5) [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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
