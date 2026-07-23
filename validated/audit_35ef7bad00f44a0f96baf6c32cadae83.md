Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address as Swapper, Allowing Any User to Bypass Per-User Swap Restrictions When Router Is Allowlisted — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap`, so `sender = router`. If a pool admin allowlists the router address to enable router-mediated swaps for their intended users, any user—including unauthorized ones—can bypass the per-user restriction by routing through the router, because the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][end_user]`.

## Finding Description

In `MetricOmmPool.swap`, `msg.sender` is passed as `sender` to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards `sender` to the extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` = pool, `sender` = direct caller of `pool.swap`. In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

So `sender = address(router)`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The actual end user (`msg.sender` of `exactInputSingle`) is never checked. The same applies to `exactOutputSingle` (L135-137), `exactInput` (L103-112), and `exactOutput` (L165-181).

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a specific set of users (e.g., KYC-gated).
2. Admin calls `setAllowedToSwap(pool, router, true)` to allow allowlisted users to swap via the router.
3. Any unauthorized user calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The pool receives `pool.swap(...)` with `msg.sender = router`, so `sender = router`.
5. The extension sees `allowedSwapper[pool][router] == true` and permits the swap.
6. The unauthorized user successfully swaps, bypassing the per-user restriction entirely.

No existing guard prevents this: the extension has no mechanism to recover the original end user from the router call, and the router stores the payer in transient storage only for the payment callback, not for access control.

## Impact Explanation
The `SwapAllowlistExtension` allowlist invariant is broken: `allowedSwapper[pool][user]` is intended to gate swaps by the actual end user, but when the router is used, it gates by the router address instead. Any unauthorized user can swap on a pool that is supposed to be access-controlled (e.g., KYC-gated, whitelist-only, or compliance-restricted pools). This constitutes broken core pool functionality—the access control extension does not protect against unauthorized swaps when the router is involved.

## Likelihood Explanation
The condition requires the pool admin to have allowlisted the router address. This is a natural and expected administrative action: an admin who wants to allow their permitted users to swap via the router would allowlist the router. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges—they simply call any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the pool. The attack is repeatable and requires no special setup beyond the router being allowlisted.

## Recommendation
Pass the original end user through the call chain so the extension can check the actual swapper. One approach: have the router encode the end user (`msg.sender`) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when `sender` is a known router. A cleaner approach is to add an `originSender` field to the `beforeSwap` hook arguments that the pool populates from a trusted source (e.g., transient storage set by the router before calling `pool.swap`). Alternatively, document that allowlisting the router grants access to all users and require admins to use `allowAllSwappers` instead, removing the false sense of per-user control.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension configured.
// 2. Admin allowlists only `authorizedUser` for direct swaps:
//    extension.setAllowedToSwap(pool, authorizedUser, true);
// 3. Admin also allowlists the router (to let authorizedUser use it):
//    extension.setAllowedToSwap(pool, address(router), true);

// Attack:
// 4. `unauthorizedUser` calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: unauthorizedUser,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Result: swap succeeds because extension checks allowedSwapper[pool][router] == true,
// never checking allowedSwapper[pool][unauthorizedUser].
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
