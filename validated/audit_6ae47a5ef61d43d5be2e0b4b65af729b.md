Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the actual user, allowing full allowlist bypass — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so `sender` passed to the extension is the router address, not the actual user. Any pool that allowlists the router — the expected operational step for supporting standard periphery swaps — opens itself to all users regardless of individual allowlist status.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the first argument the pool passes to the hook. In `MetricOmmPool.swap`, the pool calls:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    ...
```

`ExtensionCalling._beforeSwap` then encodes this as the first argument to `IMetricOmmExtensions.beforeSwap`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    ...
```

So `msg.sender` inside `pool.swap()` is the **router address**. The extension receives `sender = router`, checks `allowedSwapper[pool][router]`, and the actual user's address is never evaluated. The same binding applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165) — all call `pool.swap()` directly with the router as `msg.sender`.

The existing guard is structurally insufficient: it correctly identifies the pool (`msg.sender` in the extension = pool) but uses the wrong actor for the per-user check (`sender` = router, not user).

## Impact Explanation
A pool admin deploying `SwapAllowlistExtension` to create a curated pool (e.g., KYC-gated, institution-only) will call `setAllowedToSwap(pool, routerAddress, true)` to enable standard periphery UX. This single action makes `allowedSwapper[pool][router] == true`, causing `beforeSwap` to pass for **every** user who routes through the router, regardless of individual allowlist status. The admin has no mechanism to express "allow the router but only for allowlisted users" — the extension cannot distinguish between different users arriving through the router. Any unauthorized user can trade in the restricted pool, receiving tokens at oracle-anchored prices intended only for the curated counterparty set. This is a direct loss of curation policy and potentially of LP principal if the pool's pricing or fee structure was calibrated for a specific counterparty set. **Severity: High.**

## Likelihood Explanation
Allowlisting the router is the expected and routine operational step for any pool that wants to support the standard periphery swap path. A pool admin who deploys `SwapAllowlistExtension` to restrict swappers will almost certainly also allowlist the router to avoid breaking the standard UX. The bypass is triggered by a well-motivated, non-exotic admin action. Any curated pool that also supports router-based swaps is affected. **Probability: Medium-High.**

## Recommendation
The `beforeSwap` hook must gate on the economic actor, not the technical caller. The preferred fix is a trusted-router pattern: maintain a registry of trusted routers in the extension; when `sender` is a known router, decode the real user from a standardized field in `extensionData` (e.g., `abi.encode(realUser)`); check the decoded address against `allowedSwapper`. The router must be updated to always populate this field with `msg.sender`. Alternatively, the router could be updated to pass the real user as `recipient` and the extension could check `recipient`, but this breaks for multi-hop paths where intermediate recipients are contracts.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin: extension.setAllowedToSwap(pool, alice, true)   // alice is individually allowed
  admin: extension.setAllowedToSwap(pool, router, true)  // router allowed for standard UX

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
        pool: pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: X,
        ...
    })

Execution trace:
  router.exactInputSingle()                          // msg.sender = bob
    → pool.swap(bob, true, X, ...)                   // msg.sender = router
      → _beforeSwap(router, bob, ...)
        → extension.beforeSwap(router, bob, ...)     // msg.sender = pool, sender = router
          → allowedSwapper[pool][router] == true ✓   // no revert
      → swap executes, bob receives tokens

Result: bob swaps successfully despite not being in the allowlist.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, call `router.exactInputSingle` as `bob`, assert no revert and tokens transferred to `bob`. [1](#0-0) [2](#0-1) [3](#0-2)

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
