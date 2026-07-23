Audit Report

## Title
SwapAllowlistExtension gates on router address instead of end user, enabling full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which resolves to `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. A pool admin who allowlists the router to enable router-based swaps for approved users inadvertently opens the pool to all users, completely bypassing the per-user swap allowlist.

## Finding Description
The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` is called, the router calls `pool.swap(...)` directly, making `msg.sender` to the pool the router contract, not the end user: [4](#0-3) 

The same applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181) — all call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

The extension therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Once the router is allowlisted, any address calling through the router passes the check.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and gates on `owner` (the economic actor), demonstrating the intended pattern: [6](#0-5) 

The swap extension has no equivalent `owner`-style parameter to fall back on — the `sender`/`recipient` pair in the swap hook does not carry the end-user identity when a router intermediates.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of users (e.g., KYC'd counterparties, institutional traders) is fully bypassed once the router is allowlisted. Any unprivileged address can call `exactInputSingle` or any other router entry point and execute swaps against LP funds in a pool they were never authorized to access. This constitutes a broken core pool access-control mechanism causing direct loss of LP assets — unauthorized users can drain liquidity at oracle prices from a restricted pool.

## Likelihood Explanation
The scenario is operationally natural: a pool admin deploys a curated pool with `SwapAllowlistExtension`, allowlists specific users, then allowlists the router so those users can benefit from slippage protection and multi-hop routing. There is no on-chain signal that allowlisting the router is semantically equivalent to calling `setAllowAllSwappers(pool, true)`. The bypass requires no privileged access and is repeatable by any address. The attacker only needs to call the public router entry points.

## Recommendation
Gate on the economic actor, not the immediate caller. The `DepositAllowlistExtension` pattern (checking `owner`, not `sender`) is the correct model. For swaps, the equivalent requires the end-user identity to be conveyed through `extensionData` — the router should populate a signed or otherwise unforgeable attestation of `msg.sender` (the real user) in `extensionData`, and the extension should verify it. Alternatively, document explicitly that allowlisting the router is equivalent to opening the pool to all users, so admins can make an informed decision. The current NatDoc on `SwapAllowlistExtension` contains no such warning.

## Proof of Concept
1. Pool admin deploys pool with `SwapAllowlistExtension` as `EXTENSION_1`, `beforeSwap` order set to extension 1.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — intending only Alice to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` — intending Alice to use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, bob, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes against LP funds in a pool he was never authorized to access.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
