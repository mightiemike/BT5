Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, but `MetricOmmPool.swap` populates `sender` with `msg.sender` — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the originating user. Any pool admin who allowlists the router (a necessary step for allowlisted users to benefit from the router) inadvertently opens the pool to all users on-chain.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` and `exactInput` call `pool.swap(...)` directly, making the router `msg.sender` to the pool: [4](#0-3) [5](#0-4) 

The router has no user-level access control; any address may call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. There is no mechanism in the router or the extension to recover the originating user's identity. The extension's `allowedSwapper` and `allowAllSwappers` mappings provide no path to distinguish "router acting for an allowlisted user" from "router acting for an arbitrary user": [6](#0-5) 

## Impact Explanation

This is an admin-boundary break / access-control bypass. `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade in a pool. Once the router is allowlisted — a necessary configuration for allowlisted users to use the router — the guard is completely neutralised for all public callers. Any user can execute swaps in a pool the admin intended to be restricted, potentially extracting value from pools with subsidised pricing, bypassing KYC/AML controls, or trading in pools designed for specific counterparties.

## Likelihood Explanation

The bypass requires the pool admin to explicitly allowlist the router. This is a natural and expected configuration step: any admin who wants allowlisted users to benefit from multi-hop or slippage-protection features must allowlist the router. The extension's interface gives no indication that `sender` will be the router rather than the originating user, so the admin has no way to know that doing so opens the gate to all users. The condition is reachable by any unprivileged on-chain caller with no special permissions.

## Recommendation

The extension must resolve the originating user's identity rather than trusting the immediate caller. Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: Have the router encode the originating user (`msg.sender` at router entry) in `extensionData`; have the extension decode and verify it. This requires the router to be a trusted, non-spoofable forwarder (e.g., verified against a factory-registered router registry).
2. **Check `recipient` instead of `sender`**: For swap allowlists the economically relevant actor is the one who receives output tokens; checking `recipient` survives router indirection. This changes the semantic from "who initiates" to "who receives."

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is intended to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. Router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps in a pool he was never meant to access.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
