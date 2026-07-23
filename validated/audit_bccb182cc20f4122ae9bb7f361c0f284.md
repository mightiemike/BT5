Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on router address instead of originating EOA, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` equals the router contract address, not the originating EOA. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently grants swap access to every user, completely defeating the allowlist.

## Finding Description

**Root cause — `sender` is the immediate caller of `pool.swap()`, not the originating EOA.**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is the immediate caller of `pool.swap()`.

**Exploit path via `MetricOmmSimpleRouter`.**

`exactInputSingle` calls `pool.swap()` directly from the router's context:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The pool's `msg.sender` is therefore the router contract. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_EOA]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Bypass scenario:**
1. Pool admin deploys pool with `SwapAllowlistExtension`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — alice is the intended gated user.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so alice can use the router.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap()` → pool's `msg.sender` = router.
6. Extension evaluates `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes, bypassing the allowlist entirely.

**Contrast with `DepositAllowlistExtension`**, which correctly avoids this problem by checking `owner` — an address explicitly supplied by the caller — rather than `sender`:

```solidity
// DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

There is no analogous explicit-owner field in the swap interface, and no mechanism to recover the originating EOA from `extensionData` or any other channel in the current extension.

## Impact Explanation

The `SwapAllowlistExtension` is a core access-control primitive. Its entire purpose is to restrict which addresses may trade on a curated pool. The bypass allows any unprivileged address to execute swaps on a pool that the admin intended to be gated, constituting broken core pool functionality and an admin-boundary break. The pool admin cannot simultaneously allow legitimate users to use the router and enforce the allowlist — both outcomes (open pool or broken router path) are fund-impacting.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the documented, supported public swap entrypoint. Any pool admin who configures a swap allowlist and wants approved users to trade via the router must allowlist the router — there is no other supported path. This is the expected operational configuration, making the bypass reachable by any unprivileged user on any allowlisted pool that also permits router access. No special privileges or unusual conditions are required.

## Recommendation

The extension must gate the originating user, not the intermediary. Two viable approaches:

1. **Encode the real sender in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router check (verify `sender` is a factory-registered router before trusting the decoded address).

2. **Add an explicit `originalSender` field to the swap interface**: The pool passes both `msg.sender` (the immediate caller) and an `originalSender` (the EOA, defaulting to `msg.sender` for direct calls, set by the router for mediated calls). Extensions then check `originalSender`. This mirrors how `DepositAllowlistExtension` uses `owner`.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin: setAllowedToSwap(pool, alice, true)
3. Pool admin: setAllowedToSwap(pool, router, true)  // required for alice to use router
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) — pool's msg.sender = router
6. Pool calls _beforeSwap(router, ...)
7. Extension evaluates allowedSwapper[pool][router] → true
8. Bob's swap executes successfully — allowlist bypassed
```

A Foundry integration test can confirm this by: deploying the extension and pool, configuring the allowlist for alice and the router, then asserting that a `pool.swap()` call from an address that is neither alice nor the router reverts, while the same swap routed through `MetricOmmSimpleRouter` from that same address succeeds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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
