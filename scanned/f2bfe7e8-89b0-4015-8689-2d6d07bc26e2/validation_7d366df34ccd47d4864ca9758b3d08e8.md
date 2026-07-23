### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through the public `MetricOmmSimpleRouter`, `msg.sender` of the pool call is the router contract, not the end user. If the pool admin allowlists the router address (required for any router-mediated swap to succeed), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call chain for a direct swap (allowlist works as intended):**

```
EOA → pool.swap()
  msg.sender = EOA
  _beforeSwap(sender=EOA, ...)
  SwapAllowlistExtension.beforeSwap(sender=EOA)
    allowedSwapper[pool][EOA]  ← correct identity checked
```

**Call chain for a router-mediated swap (allowlist broken):**

```
EOA → MetricOmmSimpleRouter.exactInputSingle()
  router → pool.swap(recipient, ...)
    msg.sender = router
    _beforeSwap(sender=router, ...)
    SwapAllowlistExtension.beforeSwap(sender=router)
      allowedSwapper[pool][router]  ← router identity checked, not EOA
```

In `MetricOmmPool.swap`, the pool unconditionally passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool — the router, not the end user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

The same substitution occurs in `exactInput`, `exactOutputSingle`, and `exactOutput`, and in the recursive `_exactOutputIterateCallback` inner hops: [5](#0-4) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC-verified counterparties, protocol-owned bots, or whitelisted market makers). To allow those legitimate users to use the supported periphery router, the admin must allowlist the router address. Once the router is allowlisted, **any** unprivileged EOA can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the restricted pool — the extension sees `sender = router` and passes the check. The allowlist provides zero protection against router-mediated swaps. LP funds are exposed to the full universe of traders the admin intended to exclude, including toxic flow, arbitrageurs, or non-compliant counterparties.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, publicly deployed periphery swap entrypoint. Any user who discovers the pool is restricted can trivially route through the router. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. The likelihood is high whenever a pool admin both enables the swap allowlist and needs router-based access for legitimate users.

---

### Recommendation

The extension must verify the **original end user**, not the immediate caller of the pool. Two sound approaches:

1. **Pass the original initiator through the router.** Add an `originator` field to the `extensionData` payload that the router populates with `msg.sender` before calling the pool. The extension reads and verifies this field. This requires the router to be a trusted forwarder, which it already is for payment purposes.

2. **Gate on `recipient` instead of `sender` for swap allowlists.** If the pool's design intent is to restrict who *receives* output tokens, check `recipient`. If the intent is to restrict who *initiates* the trade, option 1 is required.

3. **Document that the allowlist only applies to direct pool calls** and that router-mediated swaps are ungated, so pool admins do not deploy a false sense of security.

---

### Proof of Concept

```solidity
// Pool admin sets up a restricted pool
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Only allowedUser is supposed to swap
ext.setAllowedToSwap(address(pool), allowedUser, true);
// Admin must also allowlist the router so allowedUser can use it
ext.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not on allowlist) bypasses the gate:
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);
// This succeeds — extension sees sender=router, which is allowlisted
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Attacker successfully swapped on a pool they were not allowlisted for
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
