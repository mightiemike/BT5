Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]`, where `sender` is the pool's `msg.sender` at swap time. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, not the originating EOA. Any pool admin who allowlists the router (required for vetted users to swap via the router) simultaneously grants every unprivileged address the ability to bypass the allowlist by routing through the router.

## Finding Description

`ExtensionCalling._beforeSwap` forwards the pool's `msg.sender` as the `sender` argument to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly — making the router the pool's `msg.sender`: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The extension config (`ExtensionOrders`, `PoolExtensions`) is stored as immutables in `ExtensionCalling` and cannot be changed post-deployment: [5](#0-4) 

The pool admin faces an impossible choice: if the router is not allowlisted, even vetted users cannot swap through the router (the normal UX path). If the router is allowlisted, the check evaluates `allowedSwapper[pool][router] == true` for every caller, and the allowlist is completely bypassed.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that guarantee entirely the moment the router is allowlisted. Any unprivileged address can trade on the restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This is a direct policy bypass: the pool may carry special pricing, subsidized spreads, or regulatory constraints that are supposed to apply only to vetted counterparties, resulting in fund-impacting consequences for the pool and its LPs.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the production entry point for swaps. Any pool admin who wants allowlisted users to use the router must allowlist the router, at which point the bypass is trivially reachable by any address. No special privilege, flash loan, or unusual token behavior is required — a single public call to the router suffices.

## Recommendation

The pool must forward the originating user's address, not its own `msg.sender`, as the `sender` argument to extensions. Two viable approaches:

1. **Router encodes originating user via `extensionData`**: Define a convention where the router encodes `msg.sender` (the actual user) into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of (or in addition to) the `sender` parameter.

2. **Pool exposes a `swapFrom(address originatingUser, ...)` entry point**: The router calls this variant, which passes `originatingUser` as `sender` to extensions. The pool verifies `msg.sender` is a trusted router before accepting the override.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the vetted user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: restrictedPool,
           tokenIn: token0,
           tokenOut: token1,
           zeroForOne: true,
           amountIn: X,
           ...
       })
5. router calls pool.swap(bob_recipient, ...)
6. pool calls _beforeSwap(router_address, ...)
7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
8. Bob's swap executes on the restricted pool — allowlist fully bypassed.
```

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L37-51)
```text
  constructor(PoolExtensions memory extensions, ExtensionOrders memory extensionOrders) {
    EXTENSION_1 = extensions.extension1;
    EXTENSION_2 = extensions.extension2;
    EXTENSION_3 = extensions.extension3;
    EXTENSION_4 = extensions.extension4;
    EXTENSION_5 = extensions.extension5;
    EXTENSION_6 = extensions.extension6;
    EXTENSION_7 = extensions.extension7;
    BEFORE_ADD_LIQUIDITY_ORDER = extensionOrders.beforeAddLiquidity;
    AFTER_ADD_LIQUIDITY_ORDER = extensionOrders.afterAddLiquidity;
    BEFORE_REMOVE_LIQUIDITY_ORDER = extensionOrders.beforeRemoveLiquidity;
    AFTER_REMOVE_LIQUIDITY_ORDER = extensionOrders.afterRemoveLiquidity;
    BEFORE_SWAP_ORDER = extensionOrders.beforeSwap;
    AFTER_SWAP_ORDER = extensionOrders.afterSwap;
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
