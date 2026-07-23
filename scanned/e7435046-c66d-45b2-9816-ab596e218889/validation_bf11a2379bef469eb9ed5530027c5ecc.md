### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` inside `MetricOmmPool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the originating user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user on the network can bypass the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses `msg.sender` (the pool) as the mapping key and `sender` (the direct pool caller) as the identity being checked: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`, and for the recursive callback path in `_exactOutputIterateCallback`: [5](#0-4) [6](#0-5) 

In every router-mediated swap, the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router address (the only way to make router swaps work on a curated pool) simultaneously opens the pool to every user on the network.

---

### Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is designed to restrict trading to a specific set of addresses. The bypass is total: once the router is allowlisted, the guard returns `IMetricOmmExtensions.beforeSwap.selector` for every caller regardless of their individual allowlist status. Non-allowlisted users can drain LP inventory at oracle prices, execute arbitrage, or otherwise interact with a pool that was explicitly closed to them. This constitutes a direct loss of the pool's curation guarantee and, depending on pool composition, a direct loss of LP principal through unwanted trades.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface documented and deployed by the protocol. A pool admin who wants to allow any allowlisted user to swap through the router must allowlist the router address, because the extension only sees the router as `sender`. This is the natural, expected configuration. The bypass therefore activates under normal operational setup, not under adversarial or exotic conditions.

---

### Recommendation

The extension must check the economically relevant actor, not the intermediary. Two sound approaches:

1. **Pass the originating user through the router.** The router stores `msg.sender` in transient storage as the payer. The pool could accept an explicit `originator` argument, or the extension could read it from a trusted router context. However, this requires core changes.

2. **Check `sender` only when the caller is a known router; otherwise check `sender` directly.** This is fragile.

3. **Simplest fix — check `sender` for direct calls and require the router to forward the real user identity via `extensionData`.** The extension decodes the real user from `extensionData` when `sender` is a known router, and checks `sender` directly otherwise.

The cleanest fix matching the LooksRare recommendation pattern: define a fixed interface that all callers of the pool must implement to attest the originating user, and have the extension verify that attested identity rather than the raw `sender`.

---

### Proof of Concept

```solidity
// Setup: pool admin creates a curated pool with SwapAllowlistExtension
// and allowlists the router so that router-mediated swaps are possible.
swapAllowlist.setAllowedToSwap(pool, address(router), true);
// alice is NOT individually allowlisted
// swapAllowlist.setAllowedToSwap(pool, alice, false); // default

// Attack: alice bypasses the allowlist by going through the router
vm.prank(alice);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        recipient: alice,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// The beforeSwap hook checks allowedSwapper[pool][router] == true → passes.
// Alice receives token1 from a pool she was never authorized to trade on.
```

The extension checks `allowedSwapper[pool][router]` (true) instead of `allowedSwapper[pool][alice]` (false), so the guard passes and the swap executes. [7](#0-6) [8](#0-7) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
