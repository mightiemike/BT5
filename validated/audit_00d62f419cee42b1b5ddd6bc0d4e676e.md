Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. A pool admin who allowlists the router to support router-mediated swaps inadvertently grants every user of that router unrestricted access to the pool, fully bypassing the per-user allowlist.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`/`exactOutputSingle`/`exactOutput`) calls the pool, the pool's `msg.sender` is the router. The actual end-user address is stored only in transient callback context (via `_setNextCallbackContext`) for payment purposes and is never forwarded to the pool or the extension: [4](#0-3) 

The `beforeSwap` hook interface provides only `sender` (the immediate caller of `swap()`) and `recipient` — no separate `owner`/`initiator` field exists: [5](#0-4) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the economic actor explicitly passed by the pool), not `sender` (the intermediary), because the liquidity hook interface carries both fields: [6](#0-5) 

The swap hook has no equivalent `owner` field, creating a structural gap that makes `SwapAllowlistExtension` unable to distinguish "router called by an allowlisted user" from "router called by a non-allowlisted user."

## Impact Explanation

When a pool admin allowlists the router address (the natural operational step to enable router-mediated swaps for permitted users), `allowedSwapper[pool][router] == true`. Any unprivileged user who calls `router.exactInputSingle()` will have their swap pass the extension check, because the extension sees `sender = router`. The allowlist is fully bypassed for all router-mediated swaps. Pools intended to be restricted (e.g., for KYC gating, regulatory compliance, or LP-controlled access) will accept swaps from every user of the public router. This constitutes broken core pool guard functionality with direct fund-flow consequences: the pool transacts with actors the LP/admin explicitly intended to exclude.

## Likelihood Explanation

The bypass requires the pool admin to allowlist the router — a natural and expected administrative action for any pool that wants to support the standard periphery router alongside an allowlist. The admin is not acting maliciously; they are following the expected operational pattern. The bug silently grants access to every router user, not just the intended subset. Any unprivileged user can then exploit this by simply calling the public router. No special privileges, flash loans, or timing are required.

## Recommendation

The `SwapAllowlistExtension` must gate on the actual end-user identity, not the intermediary. Two approaches:

1. **Pass the real user via `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension but avoids interface changes.

2. **Add an `initiator` field to the `beforeSwap` hook signature:** Align the swap hook with the deposit hook by adding an explicit `owner`/`initiator` parameter so the pool can forward the original user's address independently of `msg.sender`. This is a breaking interface change but closes the gap permanently.

Until fixed, pool admins must not allowlist the router address on pools with a swap allowlist, and must document that allowlisted users cannot use the router.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — intending to enable router-mediated swaps for allowlisted users.
3. Non-allowlisted userC calls:
     router.exactInputSingle({pool: pool, tokenIn: ..., zeroForOne: true, ...})
   → router calls pool.swap() with msg.sender = router
   → pool calls _beforeSwap(sender=router, ...)
   → extension.beforeSwap(sender=router, ...)
   → allowedSwapper[pool][router] == true → passes
   → userC's swap executes successfully despite not being on the allowlist.
4. userC calls pool.swap() directly:
   → extension.beforeSwap(sender=userC, ...)
   → allowedSwapper[pool][userC] == false → reverts NotAllowedToSwap.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist the router, call `router.exactInputSingle` from an address not in `allowedSwapper`, and assert the swap succeeds. Then call `pool.swap` directly from the same address and assert it reverts with `NotAllowedToSwap`.

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
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
