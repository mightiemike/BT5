Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router to enable router-based trading for approved users, every non-allowlisted address can bypass the curated-pool restriction by calling the same public router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards this value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `sender` against the per-pool allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with no mechanism to forward the original `msg.sender` — the router stores the original caller only in transient storage for the payment callback, not for extension visibility: [4](#0-3) 

The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. When the path is `user → MetricOmmSimpleRouter → pool.swap()`, `sender` received by the extension is the **router address**, not the user. This creates an irreconcilable conflict: if the router is not allowlisted, no allowlisted user can trade through it; if the router is allowlisted, every non-allowlisted user can bypass the guard by calling the public router.

The existing `onlyPool` guard in `BaseMetricExtension` only verifies that `msg.sender` is a registered pool — it does not validate the identity of the economic actor: [5](#0-4) 

No existing guard in the call chain recovers or validates the true initiator.

## Impact Explanation
A curated pool deployer uses `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC-verified counterparties or whitelisted market makers). Once the pool admin allowlists the router to enable router-based trading for their approved users, every non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle()` and trade against the pool's liquidity without restriction. The allowlist guard is completely neutralized. LP funds are exposed to the full public swap flow that the pool admin explicitly intended to gate. This constitutes a broken core pool functionality causing direct loss of the access-control invariant the pool admin paid to enforce, and exposes LP principal to unrestricted public swap flow — a High severity impact under Sherlock thresholds.

## Likelihood Explanation
The trigger requires only that the pool admin allowlists the router — a natural and expected operational step for any curated pool that wants to support the standard periphery. No privileged escalation, no malicious setup, and no non-standard tokens are required. Any unprivileged address can then call the public router functions. The router is a deployed, public, permissionless contract.

## Recommendation
The `sender` forwarded to `beforeSwap` must represent the economic actor, not the intermediary. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: store the original `msg.sender` in transient storage and expose it via a standard interface so extensions can read the true initiator alongside the existing callback context.
2. **In `SwapAllowlistExtension.beforeSwap`**: read the true initiator from the router's transient context when `sender` is a known router, or require pools to pass the end-user address explicitly through `extensionData`.

Alternatively, document and enforce at the factory level that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` by reverting pool creation that combines both.

## Proof of Concept
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` configured as `beforeSwap` hook.
2. Pool admin allowlists Alice: `swapExtension.setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `swapExtension.setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` — `msg.sender` inside the pool is `router`.
6. `_beforeSwap(sender=router, ...)` is dispatched; `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. Bob's swap executes against the curated pool's liquidity, bypassing the allowlist entirely.

A Foundry test can reproduce this by deploying `SwapAllowlistExtension`, a pool with it as `beforeSwap` hook, allowlisting only Alice and the router, then calling `exactInputSingle` as Bob and asserting the swap succeeds instead of reverting with `NotAllowedToSwap`.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
