### Title
SwapAllowlistExtension Bypass via Router: Any User Can Swap on Allowlisted Pools When Router Is Permitted - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for their permitted users, every unprivileged user can bypass the allowlist entirely by calling the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of the pool call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The broken invariant:** A pool admin who wants their allowlisted users to be able to use the router must add the router address to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, regardless of whether that caller is on the per-user allowlist. The per-user allowlist is completely bypassed for all router-mediated swaps.

### Impact Explanation

Any user who is not on the per-user allowlist can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and execute swaps on a pool that was intended to be restricted. The allowlist guard — the sole mechanism preventing unauthorized trading on restricted pools — is rendered ineffective. Unauthorized swaps drain pool liquidity at oracle-quoted prices, directly harming LPs who deposited under the assumption that only vetted counterparties could trade.

### Likelihood Explanation

The trigger is a routine, expected admin action: allowlisting the router so that permitted users can access the standard periphery interface. The `MetricOmmSimpleRouter` is the primary user-facing swap entry point documented in the protocol. Any pool admin who enables router access for their allowlisted users simultaneously opens the pool to all users. No special privileges, flash loans, or unusual token behavior are required — a single `exactInputSingle` call from any EOA suffices.

### Recommendation

The extension must verify the **end user**, not the intermediary. Two sound approaches:

1. **Pass the original caller through the router.** Have the router store `msg.sender` in transient storage (as it already does for the payer) and expose it via a standard interface that extensions can query during the hook call.

2. **Check `sender` against a router-aware allowlist.** If the `sender` is a known router, the extension should read the actual payer/initiator from the router's transient context and gate on that address instead.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly passed by the caller), not on `sender`. [6](#0-5) 

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap order = extension2)
  allowedSwapper[pool][alice]  = true   // alice is a permitted user
  allowedSwapper[pool][router] = true   // admin enables router for alice

Attack:
  mallory (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: mallory, ...})

  Execution trace:
    router.exactInputSingle  (msg.sender = mallory)
      → pool.swap(...)       (msg.sender = router)
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes!)
        → swap executes, mallory receives tokens

Result:
  mallory swaps successfully on a pool she is not allowlisted for.
  The per-user allowlist is completely bypassed.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
