Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user, allowing any user to bypass a curated pool's per-user swap allowlist by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate `msg.sender` of `pool.swap()`. When users swap via `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If a pool admin allowlists the router to enable router-mediated swaps for approved users, every user who routes through the same router simultaneously bypasses the per-user allowlist, completely defeating the curation guarantee.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`_beforeSwap` forwards this value unchanged to the extension as the first argument. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct namespace key) and `sender` is `address(router)` — not the end user. All four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) call `pool.swap()` directly, making the router the `msg.sender` of every pool call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. There is no mechanism in the extension or the pool's swap signature to recover the original user when the router is the immediate caller.

`DepositAllowlistExtension.beforeAddLiquidity` does not share this flaw because it gates on the `owner` argument (the LP share recipient), which is the economically relevant actor and is correctly forwarded by `MetricOmmPoolLiquidityAdder` as the user-specified owner, not the adder's own address.

## Impact Explanation

Two mutually exclusive failure modes exist for any curated pool using `SwapAllowlistExtension`:

**Mode A — Router not allowlisted**: Allowlisted users cannot swap via `MetricOmmSimpleRouter` at all. Since the router is the canonical, factory-registered swap interface, this breaks core swap functionality for every approved user who relies on it.

**Mode B — Router allowlisted (to fix Mode A)**: `allowedSwapper[pool][router] = true` passes the check for every user who routes through the router, regardless of individual approval. Any address — including those the admin explicitly excluded — can bypass the per-user allowlist by calling `router.exactInputSingle()`. This is a complete curation failure on pools designed to restrict trading to specific counterparties (e.g., KYC-gated or institutional pools), constituting broken core pool functionality and a direct loss of the access-control guarantee the pool admin deployed the extension to enforce.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical swap interface. Any curated pool that deploys `SwapAllowlistExtension` and expects users to interact via the router will immediately encounter one of the two failure modes. No special attacker capability is required — a standard `exactInputSingle` call from any EOA suffices. The precondition (pool admin allowlisting the router to unblock approved users) is the natural and expected operational step, making Mode B trivially reachable.

## Recommendation

The extension must identify the original user, not the immediate caller. Two sound approaches:

1. **Extension-data convention**: Require the router to ABI-encode the original `msg.sender` into `extensionData` for allowlisted pools. The extension decodes and checks that address. The router already forwards caller-supplied `extensionData` unchanged, so this is backward-compatible.

2. **Dedicated router field**: Add an `originalSender` parameter to the pool's `swap` signature (or a separate hook argument) that the pool populates from a trusted router registry, and have the extension check that field.

The `DepositAllowlistExtension` pattern — checking `owner` rather than `sender` — is the correct model: gate on the economically relevant actor, not the transaction intermediary.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // only alice may swap
  admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: charlie})

  Execution path:
    router.exactInputSingle()
      → pool.swap(recipient=charlie, ...)   // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → extension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ (passes!)
        → swap executes, charlie receives tokens

Result:
  charlie successfully swaps on a pool restricted to alice only.
  The per-user allowlist is completely bypassed for any user routing through the router.
```

Foundry test plan: deploy a pool with `SwapAllowlistExtension`, allowlist only `alice` and `router`, then call `router.exactInputSingle` from `charlie` and assert the swap succeeds (no `NotAllowedToSwap` revert).