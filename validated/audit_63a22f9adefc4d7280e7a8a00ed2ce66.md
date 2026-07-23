Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate `msg.sender` of `pool.swap`. When `MetricOmmSimpleRouter` intermediates a swap, the pool receives the router address as `sender`, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every unprivileged user can bypass the allowlist by routing through the public router. No configuration simultaneously permits allowlisted users to swap via the router and blocks non-allowlisted users.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the forwarded immediate caller: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router directly calls `IMetricOmmPoolActions(params.pool).swap(...)`: [4](#0-3) 

The pool therefore receives the router address as `sender`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The `DepositAllowlistExtension` does not share this bug because it checks the `owner` argument (explicitly supplied by the caller), not `sender`: [6](#0-5) 

## Impact Explanation
**High.** The swap allowlist is the primary curation mechanism for restricted pools. Any unprivileged user can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`) instead of calling `pool.swap` directly. The bypass requires no special privilege, no malicious setup, and no admin action beyond the admin having allowlisted the router to enable normal router usage. The result is that a pool configured to restrict trading to a specific set of counterparties accepts trades from the entire public, breaking the core pool access-control functionality and constituting broken core pool functionality causing loss of funds or unusable swap flows.

## Likelihood Explanation
**High.** `MetricOmmSimpleRouter` is a standard public periphery contract with no access control on its entry points. Any user who observes that a pool has a swap allowlist can trivially route through the router. The router is the expected user-facing entry point for swaps, so the admin is likely to allowlist it. The attack requires zero special knowledge or setup beyond knowing the router address. [7](#0-6) 

## Recommendation
The extension must gate the end user, not the immediate pool caller. Two sound approaches:

1. **Pass the original initiator through the router.** Have `MetricOmmSimpleRouter` forward the original `msg.sender` (the end user) as an explicit field inside `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that field when the immediate `sender` is a known router. This requires a trust relationship between the extension and the router.

2. **Require direct pool calls for curated pools.** Require that `sender` is never a registered router address; allowlisted users must call the pool directly. This is simpler but removes router support for curated pools.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is not allowlisted

Attack:
  - Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=alice, ...)
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  → passes
  - Alice's swap executes despite not being on the allowlist

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L19-24)
```text
contract MetricOmmSimpleRouter is MetricOmmSwapRouterBase, PeripheryPayments, SelfPermit, IMetricOmmSimpleRouter {
  /// @notice Transient callback mode is not supported by this router.
  /// @param callbackMode Unrecognized mode read from transient storage.
  error InvalidCallbackMode(uint8 callbackMode);

  constructor(address weth, address factory) MetricOmmSwapRouterBase(factory) PeripheryPayments(weth) {}
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
