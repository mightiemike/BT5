### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the **router contract address**, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the allowlist to every user on-chain.

---

### Finding Description

**Root cause — wrong actor binding in the allowlist check.**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension receives `sender = router_address`. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual originating user's address is never consulted.

**Two broken outcomes result:**

| Pool admin action | Outcome |
|---|---|
| Allowlists the router to enable router swaps | Every user on-chain can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter` |
| Does not allowlist the router | Individually allowlisted users cannot use the router at all |

The same mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

There is no mechanism in the current design for the router to forward the originating user's address to the extension. The `extensionData` field is user-controlled and forwarded verbatim, but `SwapAllowlistExtension` ignores it entirely.

---

### Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is designed to restrict swaps to a specific set of trusted counterparties (e.g., KYC'd addresses, protocol-owned bots, or whitelisted market makers). If the pool admin allowlists the router — a natural and expected action to allow those trusted users to interact via the standard periphery — the allowlist is completely nullified. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and swap against the pool's LP liquidity without restriction. LPs suffer adverse selection from untrusted flow that the allowlist was specifically configured to block, resulting in direct loss of LP principal.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who configure a swap allowlist will almost certainly also allowlist the router so that their trusted users can interact normally. The bypass requires no special privileges, no flash loans, and no contract deployment — a single call to `exactInputSingle` with any pool address is sufficient. Any user who discovers the router is allowlisted can exploit this immediately.

---

### Recommendation

The extension must check the economically relevant actor — the originating user — not the intermediate router. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. This requires a coordinated change to both the router and the extension.

2. **Check `sender` only for direct pool calls; require a signed proof for router calls**: The extension inspects whether `sender` is a known router and, if so, requires a user-identity proof in `extensionData`.

The simplest safe default is to treat any call where `sender` is a known periphery contract as unauthenticated and revert unless `allowAllSwappers` is set.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, alice, true)
   — alice is the only trusted swapper.
3. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — necessary so alice can use the router.
4. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) with msg.sender = router.
6. Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] → true.
8. bob's swap executes successfully despite never being allowlisted.
```

The invariant broken: `allowedSwapper[pool][bob]` is `false`, yet bob's swap is accepted because the guard checked `allowedSwapper[pool][router]` instead — an exact structural analog to H-04's `executionData.tokenId` vs `loan.nftCollateralTokenId` mismatch, where the presented value passes the guard but the actual economic actor is different.

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
