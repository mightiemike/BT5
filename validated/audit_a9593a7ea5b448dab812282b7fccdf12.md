Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` authenticates the router address instead of the originating user, allowing any caller to bypass per-pool swap access control via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` gates pool swaps by checking the `sender` argument forwarded by the pool, which is always `msg.sender` to `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension receives `sender = router`. If the router is allowlisted (the only way to enable standard routing for a restricted pool), every unprivileged user can bypass the swap allowlist entirely by routing through the router. The core access-control invariant of the extension is broken.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

**Step 2 — `SwapAllowlistExtension.beforeSwap` checks `sender` (the direct caller of `pool.swap()`).**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — not the originating EOA.

**Step 3 — `MetricOmmSimpleRouter` is the direct caller of `pool.swap()`.**

All four entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) call `IMetricOmmPoolActions(pool).swap(...)` directly, making the router `msg.sender` to the pool: [3](#0-2) 

**Step 4 — The bypass.**

A pool admin who wants to allow standard routing must allowlist the router:
```
extension.setAllowedToSwap(pool, address(router), true);
```
Once the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every caller regardless of their identity. The extension has no mechanism to distinguish which EOA initiated the router call.

## Impact Explanation
The `SwapAllowlistExtension` is the protocol's mechanism for restricting swap access to specific counterparties. When the router is allowlisted — the only configuration that enables standard routing for a restricted pool — the guard is fully bypassed: any unprivileged user can execute swaps in a pool intended to be access-controlled. This constitutes broken core pool functionality (access-controlled swap flows become universally accessible), directly enabling unauthorized value extraction from restricted pools. This meets the "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" impact criterion.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is the standard, public, permissionless swap entry point for the protocol.
- Pool admins who deploy a `SwapAllowlistExtension` and also want users to use the router will naturally allowlist the router — this is the only way to make routing work for allowlisted users.
- Once the router is allowlisted, the bypass requires no special privileges: any EOA can call `exactInputSingle` or `exactInput` on the router with no preconditions beyond the router being allowlisted.
- The trigger is a single public call, repeatable indefinitely.

## Recommendation
The extension must gate the **original economic actor**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the original caller via `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. `SwapAllowlistExtension` decodes and verifies it. This requires a trusted-router convention and the extension to verify `msg.sender` (the pool) is a known pool before trusting the decoded address.

2. **Transient storage identity registry**: The router writes the originating caller into a transient storage slot before calling the pool; the extension reads it. This mirrors how the router already uses transient storage for callback context (`_setNextCallbackContext`), and is the most robust approach. [4](#0-3) 

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  admin allowlists router: extension.setAllowedToSwap(pool, address(router), true)
  alice = allowlisted user
  bob = non-allowlisted user (attacker)

Direct swap (blocked correctly):
  bob calls pool.swap(...) directly
  → extension receives sender=bob
  → allowedSwapper[pool][bob] == false → revert NotAllowedToSwap ✓

Router swap (bypass):
  bob calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...) with msg.sender=router
  → extension receives sender=router
  → allowedSwapper[pool][router] == true → passes ✗
  → bob's swap executes in the restricted pool
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
