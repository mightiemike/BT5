Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual end-user, rendering per-user allowlist ineffective for router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is the pool's own `msg.sender`. When `MetricOmmSimpleRouter` mediates a swap, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. This makes the allowlist either block all allowlisted users from the router (broken core functionality) or, if the admin allowlists the router to fix that, allow any user to bypass the curated pool's access control.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly with no mechanism to forward the original `msg.sender` — `params.extensionData` is passed through as-is from the caller, with no encoding of the real user identity: [3](#0-2) 

The full call chain is:
```
user → MetricOmmSimpleRouter.exactInputSingle(...)
         → IMetricOmmPoolActions(params.pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]               // ← wrong actor checked
```

The actual end-user who initiated the transaction is never visible to the extension. No existing guard compensates for this — the extension has no awareness of whether it is being called via a router, and the router makes no attempt to encode the real caller.

## Impact Explanation
Two mutually exclusive failure modes arise for any pool using `SwapAllowlistExtension` with the router:

**Mode A — Broken core functionality:** Pool admin allowlists specific users (`alice`, `bob`). Those users call `router.exactInputSingle(...)`. The extension sees `sender = router`, which is not allowlisted → `NotAllowedToSwap` revert. Allowlisted users are forced to bypass the primary periphery interface and call `pool.swap()` directly, making `MetricOmmSimpleRouter` unusable for curated pools.

**Mode B — Allowlist policy bypass:** To fix Mode A, the pool admin allowlists the router address. Now any unprivileged user — including those explicitly excluded — can call `router.exactInputSingle(...)` and the extension passes, because it only checks the router address. The entire per-user allowlist policy is nullified for router-mediated swaps.

Both modes are reachable without any malicious setup. This constitutes broken core pool functionality (Mode A) and an admin-boundary break / policy bypass (Mode B), both qualifying under the allowed impact gate.

## Likelihood Explanation
Any pool that configures `SwapAllowlistExtension` as a `beforeSwap` hook and expects users to interact via `MetricOmmSimpleRouter` is affected. The router is the primary user-facing swap interface and `SwapAllowlistExtension` is a first-class supported extension. The combination is the expected production configuration for curated pools. No special attacker capability is required — Mode A is triggered by normal usage, and Mode B requires only the natural corrective action of allowlisting the router.

## Recommendation
The extension must check the actual end-user, not the intermediary. Two viable approaches:

1. **Router encodes real user in `extensionData`:** `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` before calling the pool. `SwapAllowlistExtension` decodes and checks this value when `sender` is a known/trusted router. Requires a separate router-identity registry in the extension.

2. **Require direct pool calls for curated pools:** Document and enforce (at the extension configuration level) that `SwapAllowlistExtension` is incompatible with router-mediated swaps, and add a check in the extension that reverts if `sender` is a registered router address.

## Proof of Concept
```solidity
// Pool admin sets up curated pool
extension.setAllowedToSwap(pool, alice, true);   // alice is allowlisted
// bob is NOT allowlisted

// alice tries to swap through the router (normal user flow)
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({ pool: pool, ... }));
// REVERTS: NotAllowedToSwap — extension sees sender=router, not alice

// Admin "fixes" by allowlisting the router
extension.setAllowedToSwap(pool, address(router), true);

// Now bob (not allowlisted) bypasses the allowlist
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({ pool: pool, ... }));
// SUCCEEDS: extension sees sender=router, which is allowlisted
// bob has bypassed the curated pool's allowlist
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
