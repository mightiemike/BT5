Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end user, allowing full allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which is `msg.sender` at the pool — the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `msg.sender` at the pool is the router contract, not the end user. If the router is allowlisted (a natural operational choice), every user routing through it bypasses the per-user allowlist entirely, collapsing the curation guarantee to zero.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to all configured extensions: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` paths: [5](#0-4) [6](#0-5) [7](#0-6) 

The extension has no mechanism to recover the real end user from `extensionData` or any other source. The router stores the real payer in transient storage (`_getPayer()`) but never encodes it into `extensionData` passed to the pool.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a vetted set of addresses is fully bypassed by any user routing through `MetricOmmSimpleRouter` when the router is allowlisted. Non-allowlisted users can execute swaps, interact with the pool as if allowlisted, and drain liquidity from a curated pool. This is a direct loss of the curation guarantee — a broken core pool functionality causing potential loss of funds for LPs who deposited under the assumption that only vetted counterparties could trade.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical user-facing entry point for swaps. A pool admin deploying a curated pool who also wants standard router access will naturally allowlist the router — there is no on-chain signal that doing so voids per-user allowlist enforcement. The bypass requires no special privileges, no flash loans, and no unusual token behavior: any user with a standard ERC-20 approval to the router can trigger it.

## Recommendation
The allowlist must key authorization to the end user, not the immediate pool caller. Two complementary fixes:

1. **Pass the real payer through `extensionData`**: The router should encode the real payer address (from `_getPayer()`) into `extensionData` so `SwapAllowlistExtension` can verify the actual user rather than the router.
2. **Check `recipient` instead of `sender`**: `SwapAllowlistExtension` can be changed to check `recipient` (the address receiving tokens) instead of `sender`, since the recipient is the economically relevant party and cannot be spoofed by the router.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin allowlists the router
  allowedSwapper[pool][alice]  = false  // alice is NOT individually allowlisted

Attack:
  alice calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient=alice, ...)
    → pool msg.sender = router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true → no revert
  alice's swap executes successfully despite not being allowlisted
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
