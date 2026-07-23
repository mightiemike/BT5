Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which equals `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual user. A pool admin who allowlists the router so that permitted users can trade through it simultaneously grants unrestricted swap access to every address on the router, completely defeating the allowlist.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

Both `exactInputSingle` and `exactInput` in `MetricOmmSimpleRouter` call `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) [5](#0-4) 

The allowlist lookup therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The pool admin faces an inescapable dilemma: if the router is not allowlisted, permitted users cannot trade through it; if the router is allowlisted, every unpermitted address can trade freely through it. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

This contrasts with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks `owner` — the actual LP position owner — rather than `sender`, confirming the swap extension is checking the wrong actor: [6](#0-5) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-gated or whitelist-only pools). Once the router is allowlisted — a necessary step for any permitted user who wants to use the standard periphery — every unpermitted address can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` and trade freely on the restricted pool. The allowlist provides zero protection for router-mediated flows, which are the primary user-facing entry point. This constitutes a direct break of the pool's access-control invariant and enables unauthorized fund flows through a pool designed to be restricted. This falls under the "Admin-boundary break" and "Broken core pool functionality causing loss of funds or unusable swap flows" allowed impacts.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, documented swap interface for the protocol. Any pool admin who deploys a restricted pool and wants their permitted users to have a normal trading experience will add the router to the allowlist. The bypass is then immediately available to any unpermitted address with no special knowledge, capital, or privilege requirement. The trigger is a routine admin action, not an exotic attack, making exploitation near-certain once a restricted pool is deployed with router support.

## Recommendation

The actual initiating user must be made available to the extension. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling `pool.swap`. `SwapAllowlistExtension` decodes and checks that address. This requires a trusted router convention but no interface change to the pool.

2. **Add an `originator` field to the pool's `swap` signature**: The pool passes a separate `originator` address to extensions alongside `sender`. The router sets `originator = msg.sender`; direct callers set `originator = address(0)` (falling back to `sender`). The extension checks `originator` when non-zero.

Either way, the extension must gate the economically relevant actor — the address that initiated and will pay for the trade — not the intermediate contract that relays the call.

## Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — alice is permitted.
3. Pool admin calls setAllowedToSwap(pool, router, true) — necessary for alice to use the router.
4. Bob (not in the allowlist) calls router.exactInputSingle({pool: pool, ...}).
5. pool.swap() is called with msg.sender = router.
6. SwapAllowlistExtension.beforeSwap receives sender = router.
7. allowedSwapper[pool][router] == true → check passes.
8. Bob's swap executes on the restricted pool despite never being allowlisted.

Direct call by Bob (without the router) correctly reverts because
allowedSwapper[pool][bob] == false. The router path silently bypasses the guard.
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
