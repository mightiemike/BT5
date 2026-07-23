The call chain is fully confirmed by the code. Let me verify the exact lines in `MetricOmmPool.swap` that pass `msg.sender` as `sender`:

The evidence is complete:

1. `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
2. `ExtensionCalling._beforeSwap` forwards that `sender` directly to the extension hook [2](#0-1) 
3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — pool keyed, router-valued when routed [3](#0-2) 
4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no originating-user field [4](#0-3) 

---

Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates whether the **router** is allowlisted rather than the **end user**. Any pool admin who allowlists the router to enable normal trading inadvertently opens the allowlist gate to every address that calls the router, including addresses the admin explicitly never allowlisted.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
  ...
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension hook. `SwapAllowlistExtension.beforeSwap` then performs:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct for per-pool namespacing) and `sender` is the router. The check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The full call chain for `exactInputSingle`:
```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(...)   // msg.sender in pool = router
     → _beforeSwap(sender = router, ...)
     → SwapAllowlistExtension.beforeSwap(sender = router)
     → allowedSwapper[pool][router] → true → no revert
```

`MetricOmmSimpleRouter` has no mechanism to pass the originating user address as a distinct field; it calls `pool.swap()` directly with no `onBehalfOf` parameter. There is no existing guard that recovers the true end-user identity.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) provides **no effective restriction** once the router is allowlisted. Any unpermissioned address can call `MetricOmmSimpleRouter` and execute swaps against the pool, receiving output tokens and draining liquidity that the pool admin intended to reserve for allowlisted participants only. This constitutes a direct loss of LP assets and a complete failure of the pool's curation policy — a High-severity impact under Sherlock thresholds (broken core access-control functionality causing loss of funds to LPs and violation of the pool's intended invariant).

## Likelihood Explanation
The router is the standard, documented entry point for swaps. A pool admin who deploys a `SwapAllowlistExtension` and then allowlists the router to enable normal trading will trigger the bypass for every non-allowlisted user who uses the router. The trigger requires no special privilege — any public caller of `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` reaches the vulnerable path. The precondition (router allowlisted) is the natural operational state for any pool that intends to support router-mediated trading.

## Recommendation
The extension must resolve the true end-user identity rather than accepting the forwarded `sender` argument at face value. The safest fix is to add an explicit `onBehalfOf` field to the pool's `swap()` interface that the router populates with its own `msg.sender` before calling `pool.swap()`, and have the extension check that field. Alternatively, the router can encode the real user address in `extensionData` and the extension can decode and verify it — but this requires the router to be trusted to supply honest data and the extension to know the router's encoding format. The pool-interface approach is architecturally cleaner and closes the gap for all future router implementations.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the router so normal trading works.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)` — pool sees `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender = router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Attacker's swap executes successfully, bypassing the allowlist entirely.

Foundry test sketch:
```solidity
function test_routerBypassesSwapAllowlist() public {
    // Setup: pool with SwapAllowlistExtension, router allowlisted, attacker not
    vm.prank(poolAdmin);
    extension.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT allowlisted

    // Attacker routes through the router — should revert but does not
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({pool: address(pool), ...}));
    // swap succeeds — allowlist bypassed
}
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-165)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
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
