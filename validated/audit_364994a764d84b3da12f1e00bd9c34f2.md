Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the economic actor, allowing any user to bypass a per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the pool's own `msg.sender` — the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router so that allowlisted users can use it inadvertently opens the pool to every user on the internet, completely defeating the allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [1](#0-0) 

`msg.sender` here is the pool (enforced by `onlyPool` in `BaseMetricExtension`). `sender` is the value the pool passes as the first argument to `_beforeSwap`, which is always `msg.sender` of the pool's own `swap()` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        ...
    );
``` [4](#0-3) 

The pool's `msg.sender` is the **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

No existing guard compensates for this: the extension has no mechanism to read the real initiator from `extensionData`, and the router passes `""` as `extensionData` for the first hop of `exactInputSingle`.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a curated set (KYC'd counterparties, institutional LPs, whitelisted market makers) must allowlist the router for those users to access the pool via `MetricOmmSimpleRouter`. Once the router is allowlisted, **any unprivileged user** can call any of the four router entry points and the extension passes because `allowedSwapper[pool][router] == true`. The curated pool becomes an open pool. LP funds are exposed to swappers the admin explicitly intended to exclude, and any adverse-selection or price-impact risk the allowlist was meant to prevent is fully realized. This is a broken core access-control mechanism causing direct exposure of LP assets to unauthorized counterparties.

## Likelihood Explanation
Likelihood is high for pools that combine `SwapAllowlistExtension` with `MetricOmmSimpleRouter`. The router is the primary user-facing swap entry point in the periphery. A pool admin who wants allowlisted users to use the router has no alternative: there is no mechanism in the extension or the router to forward the original `msg.sender`. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any user simply calls the public router.

## Recommendation
The `beforeSwap` hook must gate the economic actor, not the immediate caller. Two complementary fixes:

1. **In the router:** Pass `msg.sender` (the actual user) as an explicit sender argument to `pool.swap`, or encode it in a standardized `extensionData` envelope that `SwapAllowlistExtension` can read and verify.
2. **In `SwapAllowlistExtension`:** If `sender` is a known trusted router, decode the real initiator from `extensionData` and check it against the allowlist instead of checking the router address.

The simplest correct fix is for `MetricOmmSimpleRouter` to include the real user address in `extensionData` in a standardized format, and for `SwapAllowlistExtension.beforeSwap` to decode and verify it when `sender` is a trusted router.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use `MetricOmmSimpleRouter`.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(params.recipient, ...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(router, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully despite not being on the allowlist.

**Corrupted value:** `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][bob]`. The guard passes for an actor it was never configured to permit.

A Foundry integration test can confirm this by deploying the pool with the extension, allowlisting only Alice and the router, then asserting that a `vm.prank(bob)` call to `exactInputSingle` succeeds rather than reverting with `NotAllowedToSwap`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
