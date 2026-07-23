Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the per-user allowlist via the router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract address, not the original user. Any pool admin who allowlists the router to permit router-mediated swaps simultaneously opens the allowlist to every address on-chain.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whatever the pool passed as the first argument to the hook. The pool always passes its own `msg.sender` at the point of the `swap()` call:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

At the point `pool.swap()` executes, `msg.sender` is the **router address**. The pool therefore passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The router stores the original `msg.sender` only in transient storage for the payment callback — it is never surfaced to the pool or extension.

The pool admin faces an impossible choice: allowlisting the router opens the bypass to all users; not allowlisting it means individually allowlisted users cannot use the router at all. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

The same structural flaw applies to `exactOutputSingle`, `exactInput`, and `exactOutput` — all router entry points call `pool.swap()` with the router as `msg.sender`.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC-verified addresses, institutional traders, or whitelisted market makers) can be accessed by any arbitrary address by routing through `MetricOmmSimpleRouter`. Unauthorized swappers can trade against the pool's LP liquidity, exposing LPs to adversarial flow (MEV, sandwich attacks, or directional pressure) that the allowlist was specifically deployed to prevent. This constitutes a direct loss path for LP principal — the allowlist's core invariant (only approved addresses may swap) is broken for any pool that needs to support router-mediated swaps.

## Likelihood Explanation
Medium-high. The router is the standard user-facing entry point for the protocol. Any pool admin who wants allowlisted users to be able to use the router (the normal UX) must allowlist the router address, which immediately opens the bypass to all users. The attacker requires no special privilege — only knowledge of the pool address and the router address. The attack is repeatable and permissionless.

## Recommendation
The extension must verify the **original initiating user**, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Extension-data attestation**: Require the router to embed the original `msg.sender` in `extensionData` and have the extension verify it. This requires the router to be trusted or the attestation to be signed.
2. **Dual-path check**: The extension can detect router-mediated calls (e.g., `sender` is a known router address) and fall back to a user identity embedded in `extensionData` for those paths, while checking `sender` directly for non-router callers.

The current design of checking `sender` (the immediate pool caller) is structurally incompatible with a router-mediated flow where the router is the pool's `msg.sender`.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  pool admin: allowedSwapper[pool][alice]  = true   (intended allowlist)
  pool admin: allowedSwapper[pool][router] = true   (needed for router UX)

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: ..., ...})

  router calls:
    pool.swap(recipient, zeroForOne, amount, ...)
    // msg.sender to pool = router

  pool calls:
    extension.beforeSwap(sender=router, ...)
    // msg.sender to extension = pool

  extension evaluates:
    allowedSwapper[pool][router] == true  ✓  → swap proceeds

Result:
  charlie successfully swaps against the pool.
  The per-user allowlist check on alice/bob is never reached.
  Any user can repeat this to trade against LP liquidity through unauthorized swap flow.
```

Foundry test plan: deploy `MetricOmmPool` with `SwapAllowlistExtension`, allowlist only `alice` and the router, then call `router.exactInputSingle` from an address `charlie` that is not allowlisted and assert the swap succeeds (bypassing the revert). Confirm that calling `pool.swap` directly from `charlie` reverts with `NotAllowedToSwap`.