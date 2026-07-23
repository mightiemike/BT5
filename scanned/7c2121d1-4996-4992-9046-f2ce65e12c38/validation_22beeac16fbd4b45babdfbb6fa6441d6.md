### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the actual end user. If the pool admin allowlists the router to enable router-based swaps, any user—including non-allowlisted ones—can bypass the swap allowlist entirely.

---

### Finding Description

**Root cause — wrong actor bound in the allowlist check.**

In `SwapAllowlistExtension.beforeSwap()` the guard is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension caller). `sender` is the first argument forwarded by the pool. [1](#0-0) 

The pool passes its own `msg.sender` as `sender` to every extension:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap()` encodes that value verbatim into the extension call: [3](#0-2) 

**The router path.** `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with itself as `msg.sender`; the original user's address is stored only in transient callback context and is never forwarded to the pool:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

So the extension receives `sender = router_address`. The check becomes:

```
allowedSwapper[pool][router]
```

not `allowedSwapper[pool][actual_user]`.

**The forced dilemma.** A pool admin who wants to support router-based swaps on a curated pool must allowlist the router address. Doing so grants **every user** the ability to swap through the router, regardless of individual allowlist status. There is no configuration that allows specific users to swap via the router while blocking others.

The same structural problem exists for `exactInput` (multi-hop) and `exactOutput` / `exactOutputSingle`, all of which call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

---

### Impact Explanation

A curated pool configured with `SwapAllowlistExtension` loses its access-control guarantee the moment the router is allowlisted. Any non-allowlisted address can execute swaps at oracle-derived prices on a pool that was intended to be restricted (e.g., KYC-only, institutional, or compliance-gated). This is a complete bypass of the configured guard with direct policy-level consequences: the pool's curation invariant is broken and cannot be restored without blocking all router-based swaps.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard periphery swap interface; end users are expected to use it. A pool admin who deploys a curated pool and wants to support the router will naturally allowlist the router address—this is the only way to make router swaps work. The bypass requires no special privileges, no malicious setup, and no unusual token behavior. Any user can trigger it by calling the router instead of the pool directly.

---

### Recommendation

1. **Router-side fix**: The router should forward the original caller's address to the pool, for example by encoding it in `extensionData` before calling `pool.swap()`. Extensions that need the real user identity can then decode it from `extensionData`.
2. **Extension-side fix**: `SwapAllowlistExtension` could accept a trusted-router registry and, when `sender` is a known router, decode the real user from `extensionData`.
3. **Documentation / invariant guard**: At minimum, document that allowlisting the router in `SwapAllowlistExtension` grants all users swap access, and add a factory-level warning or a separate router-aware allowlist variant.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension (no users allowlisted by default).
2. Pool admin calls:
       swapExtension.setAllowedToSwap(pool, address(router), true)
   (necessary to allow any router-based swap on the curated pool).
3. Non-allowlisted user Bob calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
4. Router calls pool.swap() with msg.sender = router.
5. Pool calls _beforeSwap(router, bob, ...).
6. Extension evaluates:
       allowedSwapper[pool][router] == true  →  passes
7. Bob's swap executes on the curated pool.
   Direct pool call by Bob (pool.swap()) would have reverted NotAllowedToSwap.
```

The bypass is reachable on every router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) because all of them call `pool.swap()` with `msg.sender = router`. [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
