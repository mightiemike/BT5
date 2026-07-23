Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router (required for any legitimate user to swap through it), every non-allowlisted user can bypass the curated allowlist by routing through the router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly without forwarding the originating user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,
  params.zeroForOne,
  MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
  priceLimitX64,
  "",
  params.extensionData
);
```

When the router calls `pool.swap()`, `msg.sender` to the pool is the router address. `ExtensionCalling._beforeSwap()` then forwards this router address as `sender` to the extension. `SwapAllowlistExtension.beforeSwap()` checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the router address. The check resolves to `allowedSwapper[pool][router]`. If the router is allowlisted, the check passes for every user who routes through it, regardless of whether the originating user is on the allowlist. The pool admin has no mechanism to allowlist the router for legitimate users while simultaneously blocking non-allowlisted users who also use the router.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Any unpermissioned user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle()` pointing at the allowlisted pool. The allowlist access-control invariant is completely nullified for any pool that permits router-mediated swaps. LP funds are exposed to unrestricted toxic flow from non-allowlisted actors at oracle-derived prices. This matches the "Allowlist path" Smart Audit Pivot: allowlist checks must cover the exact actor intended and cannot be bypassed through the router.

**Severity: High** — direct bypass of a core access-control invariant with no additional preconditions beyond using the public router.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- No special role, privileged key, or malicious setup is required.
- The attacker only needs to call `exactInputSingle()` on the router pointing at the pool.
- The pool admin cannot prevent this without removing the router from the allowlist, which breaks legitimate user access entirely.
- The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router.

## Recommendation

The `SwapAllowlistExtension.beforeSwap` hook must check the originating user, not the intermediary router. The most robust fix is to require the router to encode the originating `msg.sender` into `extensionData`, and have the extension decode and verify it. The extension should additionally verify that `sender` (the pool's `msg.sender`) is a trusted router before accepting the decoded value, preventing arbitrary callers from spoofing the originating user. Alternatively, restrict pool access so the router is never allowlisted and users must call the pool directly, but this breaks normal UX.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured. Only address `Alice` is allowlisted via `setAllowedToSwap(pool, Alice, true)`.
2. Pool admin allowlists `MetricOmmSimpleRouter` via `setAllowedToSwap(pool, router, true)` so Alice can use the router.
3. Attacker `Bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: Bob, ...})`.
4. Router calls `pool.swap(recipient=Bob, ...)` — pool's `msg.sender` is the router.
5. Pool calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
6. Extension evaluates: `allowedSwapper[pool][router] == true` → passes.
7. Bob receives tokens from the pool. Bob's address was never checked against the allowlist.

A Foundry integration test can confirm this by: deploying the pool with the extension, allowlisting only `Alice` and the router, then calling `exactInputSingle` from `Bob`'s address and asserting the swap succeeds (no revert), demonstrating the bypass.