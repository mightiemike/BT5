Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the originating user, allowing any user to bypass the per-pool swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract address, not the originating user. Any pool admin who allowlists the router (the only way to enable router-mediated swaps) inadvertently grants unrestricted swap access to every caller of the router, completely defeating the allowlist.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly, making `msg.sender` to the pool the router address: [4](#0-3) 

The same identity substitution occurs in `exactInput` (multihop) and `exactOutput`: [5](#0-4) 

The allowlist check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. For a pool with `SwapAllowlistExtension` to be usable via the router at all, the admin must add the router to the allowlist. Once the router is allowlisted, the check passes for every caller of the router, regardless of whether the originating user is on the allowlist.

By contrast, `DepositAllowlistExtension` does not share this flaw — it checks `owner` (the position owner passed explicitly by the pool), not `sender`, so the economically relevant identity is preserved: [6](#0-5) 

## Impact Explanation
Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. Pools configured with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses, specific protocols, or institutional counterparties are fully open to any caller once the router is allowlisted. Unauthorized swaps can drain LP value in pools whose liquidity was provisioned under the assumption that only vetted counterparties would trade. This constitutes a broken core pool functionality / admin-boundary break with direct LP asset loss potential.

## Likelihood Explanation
The trigger is fully unprivileged: any EOA or contract can call `MetricOmmSimpleRouter.exactInputSingle`. The precondition (router is allowlisted) is the only way to make a router-mediated swap work on an allowlisted pool, so any operator who deploys this combination is automatically vulnerable. No special timing, oracle state, or privileged setup is required beyond the normal deployment of the two components together.

## Recommendation
Pass the originating user through the call chain rather than the immediate `msg.sender`. Two concrete options:

1. **Preferred — add an `originator` field.** Extend the `beforeSwap` hook signature (or the `extensionData` convention) to carry the true originating address, and have the router populate it. The extension then checks that address instead of `sender`.

2. **Short-term — document incompatibility.** Until the hook signature is extended, `SwapAllowlistExtension` must document that it is incompatible with `MetricOmmSimpleRouter` and that pools using it must be accessed only through custom contracts that are individually allowlisted and enforce their own caller checks.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]   = true   // only alice is meant to swap
  allowedSwapper[pool][router]  = true   // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., amountOutMinimum: 0})

  Execution trace:
    router.exactInputSingle  (msg.sender = bob)
      pool.swap(...)         (msg.sender = router)
        _beforeSwap(sender = router, ...)
          SwapAllowlistExtension.beforeSwap(sender = router)
            allowedSwapper[pool][router] == true  → PASSES
        swap executes, bob receives output tokens

Result:
  Bob swaps successfully despite never being on the allowlist.
  The allowlist invariant is broken; any user can trade in the
  restricted pool by routing through MetricOmmSimpleRouter.
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
