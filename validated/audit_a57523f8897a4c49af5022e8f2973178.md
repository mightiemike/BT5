Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, making the per-pool swap allowlist fully bypassable via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `SwapAllowlistExtension.beforeSwap` checks whether the **router** is allowlisted rather than whether the **user** is allowlisted. This creates an inescapable dilemma: either the router is not allowlisted (breaking router-mediated swaps for all allowlisted users) or the router is allowlisted (allowing any unprivileged user to bypass the allowlist entirely by routing through the public router).

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension's `beforeSwap` hook via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that first argument (`sender`) against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly on behalf of the user, making the pool's `msg.sender` the router address, not the originating user: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap` directly. [5](#0-4) 

The allowlist mapping is `allowedSwapper[pool][sender]`. For all router-mediated swaps, `sender` is always the single shared router address. Two outcomes follow:

1. **Router not allowlisted**: Every router-mediated swap reverts with `NotAllowedToSwap`, even for users whose addresses are individually allowlisted. Allowlisted users are forced to call the pool directly, bypassing the official periphery.
2. **Router allowlisted** (the only way to let allowlisted users use the router): `allowedSwapper[pool][router] = true` opens the gate for every caller of the router, including addresses the pool admin explicitly never allowlisted. The allowlist is fully neutralised.

`DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the LP share recipient), which the liquidity adder sets to the originating user — not the intermediary `sender`: [6](#0-5) 

The swap extension has no equivalent forwarding of the true originator.

## Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or compliance-gated participants) can be fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter`. The bypassing user executes real swaps against the pool's liquidity, extracting token output and imposing token input obligations on the pool exactly as a legitimate allowlisted swapper would. LP principal is at risk because the pool was deployed under the assumption that only vetted counterparties would trade against it. This constitutes a broken core pool functionality causing direct loss of funds and an admin-boundary break where an unprivileged path bypasses a pool admin-configured access control.

## Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the canonical, publicly documented swap entrypoint. Any user who reads the protocol documentation will naturally use the router. No special knowledge, flash loan, or privileged access is required. The bypass is a single direct call to a public function on a deployed contract. The attack is repeatable indefinitely.

## Recommendation

The `sender` forwarded to extension hooks must represent the economic originator of the swap, not the immediate `msg.sender` of the pool call. The preferred fix is to extend `pool.swap` with an explicit `swapper` parameter distinct from `recipient`. The router populates this with its own `msg.sender` before calling the pool. The pool passes `swapper` to `_beforeSwap` instead of its own `msg.sender`. The pool must validate that only trusted periphery contracts (registered in the factory) may supply a `swapper` different from their own address, preventing arbitrary address spoofing.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can use the router at all).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).
  - Pool admin adds liquidity.

Attack:
  1. attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
       { pool, zeroForOne, amountIn, minOut, deadline, recipient: attacker, extensionData }
     ).
  2. Router calls pool.swap(attacker, zeroForOne, amountIn, priceLimit, "", extensionData).
  3. Pool sets sender = msg.sender = router address.
  4. _beforeSwap dispatches to SwapAllowlistExtension.beforeSwap(router, ...).
  5. Extension checks allowedSwapper[pool][router] == true → passes.
  6. Swap executes. attacker receives token output.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; allowlist is bypassed.
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
