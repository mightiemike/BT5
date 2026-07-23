### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted — not the actual user. Any user can bypass a curated pool's swap allowlist by routing through the public router.

---

### Finding Description

The call chain for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
     → IMetricOmmPoolActions(params.pool).swap(recipient, zeroForOne, amount, limit, "", extensionData)
          [msg.sender to pool = router address]
     → MetricOmmPool._beforeSwap(msg.sender, ...)   // msg.sender = router
     → ExtensionCalling._callExtensionsInOrder(...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmSimpleRouter.exactInputSingle`, the pool's `swap` is called directly with no forwarding of the original user: [1](#0-0) 

The pool then dispatches `_beforeSwap` with `msg.sender` (the router) as `sender`: [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes that router address as the `sender` argument forwarded to every extension: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` = pool and `sender` = router. [4](#0-3) 

The allowlist lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The guard is permanently misbound to the wrong actor on every router-mediated swap.

---

### Impact Explanation

Two mutually exclusive failure modes, both fund-impacting:

**Mode A — Allowlist bypass (High):** If the pool admin allowlists the router address (the natural step to enable router-based swaps for their users), every unprivileged user can bypass the curated allowlist by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router. The allowlist provides zero protection; any address can trade on a pool intended to be restricted.

**Mode B — Broken core swap path (Medium):** If the pool admin does not allowlist the router, every allowlisted user who attempts to swap through the router is rejected with `NotAllowedToSwap`. The supported periphery swap path is permanently broken for all legitimate users of allowlisted pools.

Both modes violate the protocol invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint reaches it.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap entrypoint documented and deployed by the protocol.
- Pool admins who configure `SwapAllowlistExtension` and want their users to use the router must allowlist the router — triggering Mode A immediately.
- Any user who discovers the router is allowlisted can exploit it with a single `exactInputSingle` call; no special privileges or setup required.
- The `exactInput` multi-hop path has the same flaw for every hop after the first (intermediate hops use `address(this)` as recipient but the pool still sees the router as `msg.sender`). [5](#0-4) 

---

### Recommendation

The extension must receive the **original user** as `sender`, not the immediate pool caller. Two complementary fixes:

1. **Router-side**: Store the original `msg.sender` in transient storage alongside the callback context and pass it as `callbackData` or a dedicated field so the pool can forward it to extensions. This mirrors how the payer is already stored in `_setNextCallbackContext`.

2. **Extension-side (defense-in-depth)**: `SwapAllowlistExtension.beforeSwap` should gate on the `recipient` or a user identity extracted from `extensionData` when `sender` is a known router, or the protocol should define a standard for routers to attest the originating user.

The simplest correct fix is for the router to pass the original `msg.sender` through `extensionData` and for `SwapAllowlistExtension` to decode and check it, with the pool/router combination forming a trusted attestation channel.

---

### Proof of Concept

```solidity
// Setup: pool configured with SwapAllowlistExtension
// Pool admin allowlists the router so legitimate users can swap via router
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not individually allowlisted) calls the router directly
// The extension sees sender = address(router), which IS allowlisted → passes
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp,
    tokenIn:         token0,
    extensionData:   ""
}));
// Swap executes successfully despite attacker not being individually allowlisted.
// allowedSwapper[pool][attacker] == false, but allowedSwapper[pool][router] == true
// → guard checked the wrong actor.
``` [4](#0-3) [6](#0-5) [7](#0-6)

### Citations

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
