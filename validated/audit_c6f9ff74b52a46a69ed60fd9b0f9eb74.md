Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which equals `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` calls `pool.swap()`, that `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants swap access to every caller of that public contract, nullifying the per-user access control boundary.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that `sender` verbatim into every configured extension via `_callExtensionsInOrder`.

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])
```

where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for legitimate users.
3. Any unprivileged user — including those explicitly excluded from the allowlist — calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. The router calls `pool.swap(...)`, so `sender` = router address.
5. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The per-user allowlist is completely bypassed.

The existing guard (`allowedSwapper[pool][sender]`) is insufficient because it checks the intermediary contract, not the originating user. There is no mechanism in the extension or the pool to recover the original `msg.sender` from the router call.

## Impact Explanation

This is a direct admin-boundary break: a pool admin's configured per-user swap gate is silently nullified by any user routing through the public `MetricOmmSimpleRouter`. Any address — including those the admin explicitly excluded — can execute swaps on a restricted pool. The corrupted value is the `allowedSwapper[pool][sender]` access-control decision, which resolves to `true` for the router address instead of the intended per-user check.

## Likelihood Explanation

Likelihood is **medium**. The precondition — the pool admin adding the router to the allowlist — is the only practical way to enable router-mediated swaps on an allowlisted pool. A pool admin who wants KYC'd users to trade via the standard router will naturally add the router address, not realising this grants swap access to every caller of that public contract. The router is publicly deployed and callable by anyone without preconditions.

## Recommendation

The `SwapAllowlistExtension` must gate the **end user**, not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: Require routers to encode `msg.sender` (the end user) into `extensionData`, and have the extension decode and check that address instead of (or in addition to) `sender`.
2. **Check both router and end user**: Require that when `sender` is a known router, the extension also verifies the end user identity supplied via `extensionData`, rejecting calls that do not supply a verifiable end-user address.

The extension's `beforeSwap` signature already receives `extensionData` (the last `bytes calldata` parameter), so approach (1) is directly implementable without interface changes. [5](#0-4) 

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension; allowAllSwappers = false.
// 2. setAllowedToSwap(pool, router, true)  — admin enables router path.
// 3. setAllowedToSwap(pool, attacker, false) — attacker is explicitly excluded (or never added).
// 4. attacker calls:
//      router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}))
// 5. Router calls pool.swap(...) with msg.sender = router.
// 6. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
// 7. Attacker's swap executes despite being excluded from the per-user allowlist.
```

A Foundry integration test can confirm this by:
- Deploying the pool with `SwapAllowlistExtension` configured.
- Allowlisting only the router address.
- Calling `router.exactInputSingle` from an address not in the allowlist.
- Asserting the swap succeeds (demonstrating the bypass).

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
