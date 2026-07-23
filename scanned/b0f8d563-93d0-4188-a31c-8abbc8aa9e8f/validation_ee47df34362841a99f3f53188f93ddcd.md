The call chain is fully traceable. Let me confirm the exact `sender` binding at each hop.

The full call chain is confirmed. Here is the determination:

---

### Title
`SwapAllowlistExtension` checks router address instead of end-user identity, allowing any EOA to bypass per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument passed by the pool. `MetricOmmPool.swap` always passes `msg.sender` as that `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][EOA]`. Any user who calls `exactInputSingle` through an allowlisted router bypasses the per-user gate entirely.

### Finding Description

**Step 1 — Router calls pool directly.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` as a plain external call:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
``` [1](#0-0) 

The router is `msg.sender` to the pool. There is no mechanism to forward the original EOA identity.

**Step 2 — Pool binds `msg.sender` as `sender` for the hook.**

```solidity
// MetricOmmPool.sol line 230-231
_beforeSwap(
  msg.sender,   // <-- router address, not EOA
  ...
);
``` [2](#0-1) 

**Step 3 — Extension checks the router address.**

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][EOA]`.

**Consequence — two broken invariants:**

| Pool admin intent | What actually happens |
|---|---|
| Allowlist the router so router-mediated swaps work | Every EOA on earth can swap; per-user gate is dead |
| Allowlist specific EOAs only | Those EOAs cannot swap through the router at all; router is permanently blocked |

Neither configuration achieves the stated purpose of the extension ("Gates `swap` by swapper address, per pool").

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd users, whitelisted market makers) is fully bypassed by any EOA routing through `MetricOmmSimpleRouter`. The attacker receives pool output tokens they are not authorized to receive, draining pool reserves at the expense of LPs. This is direct loss of LP principal and broken core pool access-control functionality.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entry point. Any pool that enables `SwapAllowlistExtension` and also expects users to use the router (the normal UX path) is immediately vulnerable. No privileged access, no special token behavior, and no malicious pool setup is required — a plain `exactInputSingle` call from any EOA suffices.

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate the **economic actor**, not the immediate pool caller. Two sound approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` (the EOA) into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `recipient` instead of (or in addition to) `sender`**: For swap allowlists the recipient is often the economically relevant party. Evaluate which identity the pool admin intends to gate and document it explicitly.
3. **Document the limitation**: If the design intent is to gate the immediate pool caller only, the NatSpec must warn that router-mediated swaps will always be checked against the router address, not the end user.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_routerBypassesSwapAllowlist() public {
    // Pool admin allowlists only the router address
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Unlisted EOA routes through the router
    address unlistedEOA = makeAddr("unlisted");
    deal(address(token0), unlistedEOA, 10_000);
    vm.startPrank(unlistedEOA);
    token0.approve(address(router), type(uint256).max);

    // Should revert NotAllowedToSwap — but succeeds because
    // the extension sees sender == router (allowlisted), not unlistedEOA
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: unlistedEOA,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    vm.stopPrank();
    // Assert: swap succeeded for an address that was never allowlisted
}
```

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
