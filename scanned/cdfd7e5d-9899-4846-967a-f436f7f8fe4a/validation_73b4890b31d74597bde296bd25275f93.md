### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the actual end-user. If the pool admin allowlists the router (a natural configuration choice so that approved users can use the router), every user — including those never individually approved — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...)          // msg.sender = router
             → _beforeSwap(msg.sender, ...)   // sender = router
                 → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                     → allowedSwapper[pool][router]  // checks router, not user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router's address when the swap came through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to inject the real user's address into the `sender` slot: [4](#0-3) 

The same pattern applies to `exactInput` (all hops) and `exactOutput` (all recursive hops): [5](#0-4) 

**Concrete bypass scenario:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading to KYC-approved addresses (Alice, Bob).
2. Pool admin also calls `setAllowedToSwap(pool, router, true)` so that Alice and Bob can conveniently use the router — a natural and expected configuration.
3. Charlie (not individually approved) calls `router.exactInputSingle(...)`.
4. The extension sees `sender = router_address`, which is allowlisted → swap succeeds.
5. Charlie has bypassed the per-user allowlist entirely.

The inverse is equally broken: if the router is **not** allowlisted, Alice and Bob (individually approved) cannot use the router at all, because the extension sees `sender = router` and rejects it.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for restricting pool access to approved counterparties. When it silently gates the router instead of the actual trader, the allowlist provides no real protection: any user can route through the router to trade on a pool that was intended to be restricted. This breaks the core pool access-control invariant and allows unauthorized actors to extract value from LP positions in a pool that was designed to be gated.

---

### Likelihood Explanation

Allowlisting the router is the natural and expected configuration for any pool that wants approved users to have a good UX. A pool admin who sets up a per-user allowlist and then adds the router to that list — believing the router will only forward swaps from already-approved users — will unknowingly open the pool to all users. The mistake is easy to make because the extension's interface gives no indication that `sender` is the direct caller of `pool.swap()` rather than the economic actor.

---

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check both `sender` and a decoded user field:** The extension checks `allowedSwapper[pool][sender]` OR `allowedSwapper[pool][decodedUser]` where `decodedUser` is extracted from `extensionData` when present, falling back to `sender` for direct callers.

At minimum, the `SwapAllowlistExtension` NatSpec and pool admin documentation must warn that allowlisting the router grants unrestricted access to all users.

---

### Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension
// Admin allowlists Alice and the router (so Alice can use the router)
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Charlie is NOT allowlisted
assertFalse(swapExtension.isAllowedToSwap(address(pool), charlie));

// Charlie routes through the router — extension sees sender=router, which IS allowlisted
vm.prank(charlie);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: charlie,
    tokenIn: token0,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// Swap succeeds — allowlist bypassed
```

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
