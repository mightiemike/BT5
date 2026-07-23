### Title
`SwapAllowlistExtension` gates on the router address instead of the end user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument supplied by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for legitimate users), every address in the world can bypass the allowlist by calling through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._callExtensionsInOrder(...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `sender` (the first parameter), which at this point is the router address, not the end user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original `msg.sender` into the `sender` slot seen by extensions: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the pool's `msg.sender` is the router. [5](#0-4) 

---

### Impact Explanation

Two concrete broken states arise:

**State A — Allowlist bypass (critical path):** The pool admin allowlists the router so that legitimate users can swap through it. Because the extension checks `allowedSwapper[pool][router]` and the router is allowlisted, the check passes for *every* caller regardless of their own allowlist status. Any address — including addresses the admin explicitly excluded — can execute swaps on the restricted pool by routing through `MetricOmmSimpleRouter`.

**State B — Broken core functionality:** The pool admin does not allowlist the router. Now every router-mediated swap reverts with `NotAllowedToSwap`, even for addresses that are individually allowlisted. Allowlisted users are forced to call `pool.swap()` directly, making the public periphery router unusable for any allowlisted pool.

Both states break the core invariant that the allowlist gates the economically relevant actor. State A constitutes a direct loss of access-control integrity: restricted pools become open to all swappers, which can drain LP assets at oracle-derived prices without the pool admin's consent.

---

### Likelihood Explanation

The trigger requires no special privilege. Any user who can call `MetricOmmSimpleRouter` can reach the bypass. The router is a public, permissionless contract. The only precondition is that the pool admin has allowlisted the router (State A) or has not (State B, which breaks legitimate users). Both configurations are natural operational choices. No malicious setup is required.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the end user, not the intermediary contract. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`:** Store the original `msg.sender` in transient storage alongside the callback context (already done for the payer) and expose it as a verified field in `extensionData` so extensions can read the true initiator. Alternatively, add a dedicated `swapFor(address onBehalfOf, ...)` entry point that the pool can verify via a callback.

2. **In `SwapAllowlistExtension`:** Until the router propagates the true initiator, document that `sender` is the direct pool caller and warn that router-mediated swaps will present the router address. If a trusted-router pattern is acceptable, gate on `(sender == trustedRouter && allowedSwapper[pool][recoveredUser])` using a signed payload in `extensionData`.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension
// Admin allowlists the router so legitimate users can use it
ext.setAllowedToSwap(pool, address(router), true);
// Admin explicitly blocks attacker
ext.setAllowedToSwap(pool, attacker, false);

// Attack: attacker routes through the router
// pool.swap() sees msg.sender = router → allowedSwapper[pool][router] = true → passes
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds despite attacker being explicitly blocked
```

The `beforeSwap` check at line 37 of `SwapAllowlistExtension.sol` evaluates `allowedSwapper[pool][router]` (true) and never inspects the attacker's address, so the guard passes and the swap settles at the oracle-derived price. [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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
