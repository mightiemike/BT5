### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end-user. If the pool admin allowlists the router (the only way to let legitimate users use the standard periphery), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Pool → extension argument binding**

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**What the extension actually checks**

`SwapAllowlistExtension.beforeSwap()` looks up `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

**What the router passes as the caller**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly — so `msg.sender` inside the pool is the router contract, not the end-user: [4](#0-3) 

The same applies to `exactInput` (multi-hop) and `exactOutputSingle`: [5](#0-4) 

**The structural trap**

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Allowlist individual user addresses | Those users are blocked when they use the router (router address ≠ user address) |
| Allowlist the router address | Every unprivileged user bypasses the allowlist by routing through the router |

There is no configuration that simultaneously allows legitimate users to use the standard periphery **and** blocks unauthorized users.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` is a curated pool — its LP pricing model, fee structure, or regulatory posture assumes only approved counterparties trade. If the router is allowlisted (the only way to let approved users use the standard periphery), any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and trade in the pool. This exposes LP capital to unauthorized counterparties, breaks the pool's intended access-control invariant, and can cause direct LP losses if the pool's economics depend on a restricted trader set.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` with any pool address. The only precondition is that the pool admin has allowlisted the router — a natural and expected action for any pool that wants its approved users to use the standard periphery.

---

### Recommendation

The extension must check the economically relevant actor — the end-user — not the intermediary contract. Two viable approaches:

1. **Forward the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Check `recipient` instead of `sender`**: For swaps, `recipient` is the beneficiary. While not identical to the payer, it is user-controlled and not the router address.
3. **Document that the extension is incompatible with the router** and require direct pool calls for allowlisted pools.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin allowlists the router:
       swapExtension.setAllowedToSwap(pool, address(router), true)
   (necessary so that approved users can use the standard periphery)
3. Unprivileged user (not individually allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: attacker, ...})
4. Pool calls extension.beforeSwap(sender=router, ...)
5. Extension checks: allowedSwapper[pool][router] == true → passes
6. Swap executes. Unprivileged user has bypassed the allowlist.
```

The extension's check `allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router]`, which is `true` for all users regardless of their individual allowlist status. [6](#0-5)

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
