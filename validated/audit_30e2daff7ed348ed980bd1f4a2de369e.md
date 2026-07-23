Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the Originating User, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to support router-mediated swaps on a curated pool inadvertently grants every user on the network access, completely defeating the allowlist.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` — the direct caller of `pool.swap()` — as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165). By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` argument (the economic actor), not `sender` (the intermediary): [5](#0-4) 

The swap extension has no equivalent "original swapper" identity and therefore gates the wrong address.

## Impact Explanation

A pool admin who wants to support router-mediated swaps on a curated pool must call `setAllowedToSwap(pool, router, true)`. Once `allowedSwapper[pool][router] = true`, every user on the network can call any `MetricOmmSimpleRouter` entry point and reach the pool, regardless of whether their own address is on the allowlist. The allowlist invariant — "only allowlisted addresses may swap on a curated pool" — is completely broken for all router-mediated paths. Curated pools designed to restrict counterparties (e.g., to prevent toxic flow, enforce KYC, or limit trading to specific market makers) are exposed to unrestricted adverse-selection flow, directly threatening LP principal. This constitutes a broken core pool functionality causing loss of funds.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the protocol's official, publicly documented swap router. Any pool admin who wants users to swap conveniently will allowlist the router. The bypass requires no special knowledge, no privileged access, and no non-standard tokens — any user can call `exactInputSingle` on the router against a curated pool. The precondition (router allowlisted) is the expected operational state for any pool that uses the router.

## Recommendation

The extension must gate the original user, not the intermediary. Two viable approaches:

1. **Pass original sender through `extensionData`**: The router encodes `msg.sender` (the original user) into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Add an `originalSender` field to the pool's swap interface**: The pool accepts an explicit `originalSender` parameter (analogous to how `addLiquidity` separates `sender` from `owner`) and passes it to extensions. The router sets this to `msg.sender`.

The `DepositAllowlistExtension` pattern — checking `owner` rather than `sender` — is the correct model.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)`.
4. Alice (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `MetricOmmPool.swap` passes `msg.sender` (router) as `sender` to `_beforeSwap`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → passes.
8. Alice's swap executes against the curated pool despite never being allowlisted.

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
