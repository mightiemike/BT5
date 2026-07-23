### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the router contract, not the user. If the pool admin allowlists the router (the only way to let legitimate users use it), every unpermissioned user can bypass the allowlist by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput` (all hops) and `exactOutputSingle`: [5](#0-4) 

The extension therefore sees the router's address as the swapper identity. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the router at all (broken core functionality).
- **Allowlist the router** → every user on the network can bypass the allowlist by calling `exactInputSingle` or `exactInput` through the public router.

The `exactOutput` multi-hop path compounds this: inner hops are triggered from inside `metricOmmSwapCallback`, where `msg.sender` is the outer pool, so the inner pool's extension sees the outer pool address as the swapper — an address that is almost certainly not in any allowlist. [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated addresses can be fully bypassed by any unpermissioned user calling `MetricOmmSimpleRouter.exactInputSingle`. The user receives the same swap execution (same oracle price, same bins, same output tokens) as an allowlisted direct caller. The allowlist guard is silently voided for the entire pool, allowing unrestricted token extraction from LP positions at oracle-fair prices — a direct loss of the curation guarantee the pool admin paid to enforce.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the periphery. Any user who discovers the mismatch (or simply uses the router by default) bypasses the guard without any special privilege, custom contract, or unusual token behavior. The bypass requires a single standard router call.

---

### Recommendation

The pool must convey the original economic actor's identity to the extension. Two sound approaches:

1. **Pass the original initiator through `extensionData`**: Require the router to ABI-encode `msg.sender` (the user) into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists the economically relevant identity is the address receiving output tokens. Changing the extension to gate on `recipient` (the second argument) would correctly identify the beneficiary regardless of routing path, provided the pool admin's intent is to restrict who receives tokens rather than who initiates the call.

The cleanest long-term fix is option 1 with a standardized `extensionData` header that periphery contracts always populate with the originating user address, so extensions can rely on it.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that legitimate users can use the router)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls pool.swap(attacker, true, X, ...) with msg.sender = router
  3. Pool calls _beforeSwap(router, attacker, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true (router is allowlisted)
  5. Swap executes; attacker receives output tokens at oracle price
  6. Allowlist is bypassed; attacker was never individually permitted
```

The attacker address never appears in the allowlist. The guard passes because the router is allowlisted, and the router is a permissionless public contract callable by anyone.

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
