Audit Report

## Title
SwapAllowlistExtension checks router address instead of originating user, allowing full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is used, `msg.sender` of that call is the router contract, not the originating user. Any pool admin who allowlists the router (required for router-mediated swaps to work at all) simultaneously opens the pool to every user on the network, nullifying the allowlist entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap(), not the originating EOA
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The same pattern applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165). The pool admin faces an impossible choice: allowlist individual user addresses (router-mediated swaps revert because `sender = router ≠ user`) or allowlist the router address (every user can bypass the guard). No configuration simultaneously permits router-mediated swaps for allowlisted users while blocking non-allowlisted users.

## Impact Explanation

Any unprivileged user can bypass `SwapAllowlistExtension` on a permissioned pool by routing through `MetricOmmSimpleRouter`. The allowlist is the sole mechanism for restricting swap access on such pools. Pools configured as permissioned (e.g., restricted to specific market makers or KYC'd counterparties) become open to all callers, violating the LP's access-control invariant and exposing them to trades with counterparties they explicitly excluded. This constitutes broken core pool functionality and an admin-boundary break reachable by an unprivileged caller.

## Likelihood Explanation

Likelihood is high. The router is the standard user-facing entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension` and wants allowlisted users to use the router must allowlist the router address, which immediately opens the gate to all users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call available to any EOA.

## Recommendation

Pass the originating user's address through the extension interface. The cleanest fix is to add an `originator` field to the `beforeSwap` hook signature so extensions can gate the true initiating address regardless of intermediary. Alternatively, have the router forward the original `msg.sender` as part of `extensionData` and require the allowlist extension to decode and verify it (though this is trust-dependent on the router). A third option is to document and enforce at the factory level that `SwapAllowlistExtension` is incompatible with router-mediated swaps (e.g., revert pool creation if both are configured together).

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is intended to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(bob_recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)` at `MetricOmmPool.sol` L230.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully despite not being on the allowlist.

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist only Alice and the router, call `exactInputSingle` from Bob's address, assert the swap succeeds (demonstrating the bypass) and that Bob received output tokens.