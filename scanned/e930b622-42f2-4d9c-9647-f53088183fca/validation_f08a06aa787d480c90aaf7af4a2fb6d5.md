### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. The pool always passes its own `msg.sender` as `sender`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **end user** is allowlisted. This is the direct analog of the external bug: a guard references the wrong value at check time, making the restriction ineffective.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed in: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

The pool's `msg.sender` is the router. The original user's address is stored only in transient callback context for payment settlement and is never forwarded to the pool or to any extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The original user's address is completely invisible to the extension on every router-mediated path (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, and the recursive callback hops inside `_exactOutputIterateCallback`). [5](#0-4) 

---

### Impact Explanation

Two mutually exclusive failure modes arise, both fund-impacting:

**Mode A — Allowlist bypass (High severity).**
A pool admin allowlists the router so that their approved users can reach the pool through the standard periphery path. Because the extension checks the router address, every user who calls the router is now implicitly allowlisted, regardless of whether they appear in `allowedSwapper`. Any unprivileged address can bypass the curated-pool restriction and execute swaps, draining liquidity at oracle prices that the pool admin intended to reserve for specific counterparties.

**Mode B — Broken core functionality (Medium severity).**
A pool admin allowlists only specific EOAs and does not allowlist the router. Every allowlisted user who attempts a router-mediated swap (e.g., for slippage protection, multi-hop routing, or deadline enforcement) receives `NotAllowedToSwap`, even though they are explicitly permitted. The router path is permanently broken for all allowlisted users on that pool.

In both modes the guard fails to enforce the invariant it was configured to enforce.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and deployed by the protocol. Pool admins who configure a `SwapAllowlistExtension` will naturally want their approved users to be able to use the router. The moment they allowlist the router to restore router functionality (Mode A), the allowlist is fully bypassed. The trigger requires no privileged action beyond the pool admin's own expected configuration step; any unprivileged user can then exploit it by calling any router entry point.

---

### Recommendation

The pool's `swap` interface passes only `msg.sender` as `sender`. To fix this correctly, the router should encode the original user's address in `extensionData` using a well-known prefix, and `SwapAllowlistExtension.beforeSwap` should decode and check that address when the caller is a recognized router. Alternatively, the pool could accept an explicit `originator` parameter that the router populates with `msg.sender` before calling the pool, and the extension would check `originator` instead of `sender`. Either approach ensures the extension always gates the economically relevant actor rather than the intermediary.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary to allow router-mediated swaps for approved users.
3. Unprivileged address `attacker` (not in `allowedSwapper`) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
4. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. The pool calls `_beforeSwap(router, ...)`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
7. The swap executes. `attacker` receives output tokens. The allowlist restriction is completely bypassed.

The same path works for `exactInput`, `exactOutputSingle`, `exactOutput`, and every intermediate hop in `_exactOutputIterateCallback`. [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
