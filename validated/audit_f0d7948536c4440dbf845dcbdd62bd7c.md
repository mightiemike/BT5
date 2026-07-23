Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool, which is the `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's allowlist status rather than the end user's. If the pool admin allowlists the router (the only way to permit any router-mediated swaps), every user — including those explicitly not allowlisted — can bypass the guard by calling the router.

## Finding Description

**Root cause — wrong actor bound in the hook**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap()`: [2](#0-1) 

**Router path — sender is always the router**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the router the `msg.sender` of that call: [3](#0-2) 

The same pattern holds for `exactInput` (L104–112), `exactOutputSingle` (L136–137), and `exactOutput` (L165–181). [4](#0-3) 

**The inescapable dilemma**

| Admin action | Effect |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Does** allowlist the router | Every user — allowlisted or not — can swap through the router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same. The extension's `allowedSwapper` mapping is keyed on the direct caller of `pool.swap()`, not the economic actor.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, institutional partners) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. This is an **admin-boundary break**: the pool admin's access-control policy is rendered ineffective without any privileged action by the attacker. Non-allowlisted users can drain LP inventory and expose LP capital to counterparties the pool was explicitly designed to exclude.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract — any EOA or contract can call it.
- The only precondition is that the pool admin has allowlisted the router, which is the only way to enable router-mediated swaps for legitimate users.
- The bypass requires a single standard router call with no flash loans, callbacks, or multi-step sequences.
- The attacker needs only the pool address and token pair.

## Recommendation

The extension must check the **end user**, not the intermediary. Two complementary approaches:

1. **Pass the economic actor explicitly.** Add a `swapper` field to the `beforeSwap` interface that the router sets to `msg.sender` (the end user) before calling the pool, and have the pool forward it to the extension. This requires a coordinated interface change across pool, router, and extension.

2. **Short-term mitigation within the current interface.** `SwapAllowlistExtension` can check both `sender` (the direct caller) and, if `sender` is a known router, decode the actual user from `extensionData`. The router would ABI-encode the end user's address into `extensionData` for allowlisted pools.

The invariant to enforce: **the identity checked against the allowlist must be the address that economically controls the swap, not the contract that mechanically forwards it.**

## Proof of Concept

```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router

Attack
──────
4. bob (not allowlisted) calls:
       router.exactInputSingle({
           pool:     <pool>,
           zeroForOne: true,
           amountIn: X,
           recipient: bob,
           ...
       })

5. Router calls pool.swap(bob, true, X, ...) with msg.sender = router.

6. Pool calls _beforeSwap(sender=router, ...) → extension.beforeSwap(sender=router, ...).

7. Extension evaluates:
       allowedSwapper[pool][router] == true   ← passes because admin had to allowlist the router

8. Swap executes. bob receives tokens despite never being allowlisted.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
