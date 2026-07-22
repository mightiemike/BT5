### Title
SwapAllowlistExtension Allowlist Bypassed for All Users When Router Is Allowlisted - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, the router contract is `msg.sender` at the pool boundary — not the end user. If a pool admin allowlists the router address (the natural step to enable router-mediated swaps for permitted users), every unpermissioned user on-chain can bypass the per-user allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly — it stores the original `msg.sender` only in transient callback context for payment, but never passes it to the pool as the swap initiator: [4](#0-3) 

Therefore, when any user calls the router, the pool sees `msg.sender = router`. The extension checks `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router address (to let permitted users trade via the router), the check passes for **every caller of the router**, regardless of who they are.

The same identity collapse occurs in multi-hop `exactInput` (intermediate hops use `address(this)` as payer, still router as `msg.sender` at the pool) and in `exactOutput` recursive callbacks: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys a permissioned pool (e.g., restricted to known market makers or KYC'd counterparties) and then allowlists the router so that permitted users can trade conveniently inadvertently opens the pool to every address on-chain. Any unpermissioned user can call `exactInputSingle` / `exactInput` / `exactOutput` on the router and execute swaps that the allowlist was designed to block. This breaks the core access-control invariant of the `SwapAllowlistExtension` and, in pools where the allowlist is the primary mechanism preventing toxic or adversarial flow, exposes LP principal to unintended counterparties.

---

### Likelihood Explanation

The scenario is a natural operational step: a pool admin sets up a per-user allowlist, then realizes allowlisted users cannot use the standard router (because the router's address is not on the list), and adds the router to the allowlist. The admin has no on-chain signal that this collapses per-user granularity to "anyone who calls the router." The router is a public, permissionless contract, so the bypass is immediately reachable by any EOA or contract.

---

### Recommendation

The router should forward the originating user's identity to the pool so that extensions can gate on the true economic actor. Two options:

1. **Add a `sender` parameter to `pool.swap`** that the router populates with `msg.sender`, and have the pool pass that value (rather than its own `msg.sender`) to extensions. This requires a core interface change.

2. **Check `msg.sender` of the extension call inside the router path**: the extension could read the router's stored payer from transient storage (if the router exposes it), but this couples the extension to a specific router implementation.

The simplest safe interim fix is to document that allowlisting the router address grants access to all router users, and provide a `SenderForwardingRouter` that passes the originating user as an explicit parameter so extensions can distinguish callers.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls swapExtension.setAllowedToSwap(pool, alice, true)
    → allowedSwapper[pool][alice] = true
  pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    → allowedSwapper[pool][router] = true   ← enables router for alice
  
Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})
  
  router calls pool.swap(bob, ...)
    msg.sender in pool = router
  
  pool calls _beforeSwap(router, ...)
  
  SwapAllowlistExtension.beforeSwap(router, ...):
    allowedSwapper[pool][router] == true  ← passes
  
  bob's swap executes successfully despite not being allowlisted
```

The `SwapAllowlistExtension` unit tests only exercise direct pool calls (pool as `msg.sender`), never router-mediated calls, so this path is untested: [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
