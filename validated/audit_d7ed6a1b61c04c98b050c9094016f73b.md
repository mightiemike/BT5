Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of actual end-user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the immediate caller. When `MetricOmmSimpleRouter` is used, the pool's `msg.sender` is the router contract, not the end-user. Any pool admin who whitelists the router (required for legitimate whitelisted users to use the router) simultaneously grants every user — including non-whitelisted ones — the ability to bypass the curated allowlist entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` in `ExtensionCalling` forwards this `sender` value directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, never forwarding the original `msg.sender`: [4](#0-3) 

The same applies to `exactInput` (multi-hop): [5](#0-4) 

And `exactOutputSingle`: [6](#0-5) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The actual end-user's address is never visible to the extension. There is no existing guard that recovers the original initiator from `extensionData` or any other channel.

## Impact Explanation
A pool admin deploying a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified users). To allow those whitelisted users to use the standard router, the admin must whitelist the router address. Once the router is whitelisted, every user — including those never individually approved — can call `MetricOmmSimpleRouter.exactInputSingle` and bypass the allowlist. The curated pool's access control is silently nullified, allowing unauthorized parties to trade against LP funds in a pool explicitly restricted to certain users. This is a direct admin-boundary break: an unprivileged path (the router) defeats the pool admin's intended access control, causing unauthorized fund flows against LP positions.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. The operational path of whitelisting the router is the natural and necessary step for any admin who wants legitimate users to have normal UX. The bypass requires no special privileges, no tokens beyond the swap input, and no front-running — any user can call the router with a standard swap transaction. The condition (router whitelisted) is the expected production configuration, making this highly likely to be triggered.

## Recommendation
The extension must gate on the actual end-user, not the immediate pool caller. The cleanest production fix is to have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when `sender` is a known/trusted router. Alternatively, redesign the extension interface so the pool passes the original transaction initiator explicitly in a verified field. Using `tx.origin` is an option but introduces its own risks (e.g., compatibility with smart contract wallets). Documenting that `SwapAllowlistExtension` is incompatible with the router without a verified caller-forwarding mechanism is a necessary interim step.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — whitelists `alice`.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — required for `alice` to use the router.
4. `bob` (not whitelisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Pool executes `_beforeSwap(msg.sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `bob` successfully swaps against the curated pool despite never being individually approved.

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
