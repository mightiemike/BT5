Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` at the pool boundary, so the extension checks whether the **router** is allowlisted rather than the actual end user. If the pool admin allowlists the router to enable legitimate users to use it, every non-allowlisted address can bypass the swap gate by routing through the same public router, rendering the extension's access control completely inoperative for all router-mediated flows.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension hook:**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` ABI-encodes and forwards that `sender` to every configured extension:** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` (pool → sender):** [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is whatever address called `pool.swap` — which is the router, not the end user.

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router `msg.sender` at the pool boundary:** [4](#0-3) 

The same applies to `exactInput` (all hops): [5](#0-4) 

And `exactOutputSingle` and `exactOutput` (lines 136–137, 165–181).

**Root cause:** There is no mechanism to thread the original `msg.sender` (the end user) through the router → pool → extension call chain. The extension receives only the intermediate caller (the router), not the economic actor initiating the trade.

**Existing guards are insufficient:** The extension has no awareness of the router layer. `allowAllSwappers` is a separate flag that bypasses the check entirely. There is no transient-storage originator slot, no signed permit, and no `extensionData` convention that carries the real caller identity.

## Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses faces an irreconcilable dilemma:

1. **Router NOT allowlisted:** Allowlisted users cannot use `MetricOmmSimpleRouter` at all — every router-mediated swap reverts with `NotAllowedToSwap`. The primary supported periphery path is broken for legitimate users.
2. **Router IS allowlisted** (the only way to restore router access for legitimate users): The allowlist is completely bypassed. Any non-allowlisted address can call `router.exactInputSingle` and swap on the curated pool. The extension sees `sender = router` and passes the check unconditionally.

In scenario 2, the curated pool's swap gate is rendered inoperative for all router-mediated flows. Non-allowlisted users can execute trades the pool admin intended to block, violating regulatory or business-logic constraints the allowlist was meant to enforce. This is broken core pool functionality (the `SwapAllowlistExtension`'s sole purpose is defeated) with direct fund-impact potential on curated pools.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap entry point in the periphery. Any pool admin who wants allowlisted users to be able to use the router will naturally allowlist the router address — this is the only rational configuration to restore router access. The identity mismatch is not surfaced by any guard, revert, or documentation warning. The trigger is a single, reasonable admin action (`setAllowedToSwap(pool, router, true)`), after which the bypass is immediately available to any unprivileged address with no further preconditions.

## Recommendation

The extension must check the **original end user**, not the intermediate router. Two complementary fixes:

1. **Pass the original initiator through the router.** The router already tracks the real payer in transient storage via `_getPayer()`. Extend `extensionData` or a dedicated transient slot to carry the original `msg.sender`, and have the pool forward it as a separate `originator` argument to extensions.

2. **Alternatively, require the pool to expose the original caller.** The pool could accept an explicit `swapper` parameter (verified against `msg.sender` or a signed permit) so the extension always sees the real economic actor regardless of routing depth.

Until fixed, pool admins must be warned that allowlisting the router address opens the pool to all users, and that direct-pool-only access is the only safe configuration for the current extension design.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, zeroForOne, amount, ...)
  - pool calls _beforeSwap(msg.sender=router, ...)
  - extension checks allowedSwapper[pool][router] == true  →  PASSES
  - bob's swap executes on the curated pool despite not being allowlisted

Result:
  - SwapAllowlistExtension is completely bypassed for all router-mediated swaps
  - Any non-allowlisted user can trade on the curated pool
```

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist only `alice` and the router, then call `router.exactInputSingle` from `bob` (a non-allowlisted EOA) and assert the swap succeeds — confirming the bypass.

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
