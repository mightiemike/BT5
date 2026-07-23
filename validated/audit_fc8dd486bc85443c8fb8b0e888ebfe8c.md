Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()` — the direct caller, not the economic actor. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool records the router as `sender`. Any pool admin who allowlists the router to enable router-mediated swaps for curated users simultaneously opens the pool to every unpermissioned user who routes through the same router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`, which forwards it to every configured extension as `sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
  extensionData
);
```

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← opaque bytes; extension never decodes a user identity
  );
```

The effective allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The extension never decodes a user identity from `extensionData`. This creates an irreconcilable bind:

| Admin intent | Required config | Actual result |
|---|---|---|
| Allow curated users via router | `allowedSwapper[pool][router] = true` | **Every** user bypasses the allowlist through the router |
| Block non-allowlisted users from router | Leave router un-allowlisted | Allowlisted users cannot use the router at all |

No configuration simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from using it.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs) is fully bypassed by any unpermissioned user who routes through `MetricOmmSimpleRouter`. The attacker receives identical execution quality to an allowlisted user. This breaks the core access-control invariant of the pool and constitutes a direct policy bypass: non-allowlisted users can drain liquidity from a pool designed to serve only a restricted set of counterparties.

**Severity: High** — the bypass is unconditional once the router is allowlisted, requires no privileged access, and is reachable through the standard supported periphery path.

## Likelihood Explanation

**Medium** — the scenario requires the pool admin to allowlist the router address, which is a natural and expected configuration step for any pool that wants its curated users to access the router. The `IMetricOmmSimpleRouter` interface documents the router as a first-class supported entry point. Any production pool that enables router access for its allowlisted users is immediately vulnerable.

## Recommendation

The extension must resolve the true economic actor, not the intermediary. Two complementary approaches:

1. **Pass originating user through `extensionData`**: The router encodes `msg.sender` (the originating user) into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`. This requires a coordinated encoding convention between router and extension.

2. **Check `sender` only when it is not a registered router**: Maintain a registry of approved routers. If `sender` is a registered router, the extension falls back to checking the payer address recovered from `extensionData`; otherwise it checks `sender` directly.

The simplest safe fix is option 1: the router always appends `abi.encode(msg.sender)` to `extensionData` for each hop, and the extension decodes it as the authoritative identity to gate.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router for allowlisted users
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended curated user
  - bob is NOT allowlisted

Attack:
  1. bob calls router.exactInputSingle({pool: pool, tokenIn: token0, zeroForOne: true, ...})
  2. Router calls pool.swap(recipient=bob, zeroForOne=true, ..., extensionData="")
  3. Pool calls extension.beforeSwap(sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  → passes
  5. Swap executes; bob receives token1 output

Result: bob, a non-allowlisted address, successfully swaps on a curated pool.
Direct pool call by bob (without router) would revert with NotAllowedToSwap.
```