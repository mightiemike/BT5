### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Per-User Swap Allowlist — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router**, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `pool.msg.sender = router`, so the extension receives `sender = router` and evaluates `allowedSwapper[pool][router]`. The actual end user's address is never seen by the extension.

A pool admin who wants their allowlisted users to be able to use the router must add the router to the allowlist. The moment they do, **every** user on the network can call `exactInputSingle` (or `exactInput` / `exactOutput`) and the check passes, because the router is allowlisted and the router is always the direct pool caller.

The same structural problem exists for multi-hop `exactInput`: intermediate hops use `address(this)` (the router itself) as the payer, so the router address is again what the pool sees as `sender`: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to be a private or permissioned venue — for example, to prevent arbitrage bots, enforce KYC, or limit trading to protocol-controlled addresses. Once the router is allowlisted (the natural step to let approved users trade conveniently), the guard is completely neutralised. Any address can execute swaps against the pool's LP positions, exposing LPs to the full range of adversarial trading (arbitrage, sandwich, directional pressure) that the allowlist was meant to prevent. This is a direct loss path for LP principal.

---

### Likelihood Explanation

The scenario is reachable by any unprivileged user. The only precondition is that the pool admin has allowlisted the router — a step that is both natural and expected for any pool that wants to support the standard periphery UX. The admin has no way to simultaneously allow router-mediated swaps and enforce per-user identity checks with the current extension design. There is no warning in the extension or its interface that allowlisting the router collapses the per-user gate.

---

### Recommendation

The `SwapAllowlistExtension` must check the **economic actor** (the end user), not the transport layer (the router). Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` in `extensionData`; the extension decodes and checks that address. The pool admin allowlists end-user addresses, not the router.
2. **Separate router allowlist**: Add a two-level check — allowlisted routers may relay swaps, but only on behalf of allowlisted end users encoded in `extensionData`.

Either way, the extension's `beforeSwap` must not treat the router as the identity to gate.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension as EXTENSION_1,
   BEFORE_SWAP_ORDER = encodeExtensionOrder(1, 0, …).
2. Admin calls setAllowedToSwap(pool, userA, true)          // intended user
3. Admin calls setAllowedToSwap(pool, router, true)         // to let userA use the router

Attack
──────
4. userB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, …, recipient: userB})

5. Router executes:
       pool.swap(userB, …)          // msg.sender = router

6. Pool calls:
       extension.beforeSwap(router, userB, …)
       // sender = router

7. Extension evaluates:
       allowedSwapper[pool][router] == true  ✓

8. Swap settles. userB receives output tokens.
   The per-user allowlist was never consulted for userB.
``` [3](#0-2) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
