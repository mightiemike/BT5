Audit Report

## Title
`SwapAllowlistExtension` Validates Router Address Instead of End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which resolves to `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end user. A pool admin who allowlists the router to support standard user flows inadvertently grants unrestricted swap access to every caller, completely defeating the per-user access control the extension is designed to enforce.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the entity calling the extension), and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call.

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // <-- becomes `sender` in the extension
```

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the end user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

Therefore the allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin action | Direct swap by allowlisted user | Router swap by allowlisted user | Router swap by non-allowlisted user |
|---|---|---|---|
| Allowlist user addresses only | ✓ allowed | ✗ blocked | ✗ blocked |
| Allowlist router address | ✗ blocked (unless also listed) | ✓ allowed | **✓ allowed — bypass** |

There is no configuration that simultaneously (a) allows specific users to swap via the router and (b) blocks non-allowlisted users from doing the same. The same issue applies to multi-hop `exactInput` (intermediate hops use `address(this)` = router as caller) and `exactOutput` recursive callbacks, where the pool's `msg.sender` is again the router.

## Impact Explanation

A pool admin who deploys a swap-allowlisted pool and allowlists the router (the natural step to support standard user flows) inadvertently opens the pool to **any** caller. Non-allowlisted users can execute unrestricted swaps against a pool designed to be private or restricted to specific counterparties. This breaks the core access-control invariant of the extension and enables unauthorized extraction of LP value from a pool whose liquidity providers expected restricted access. The corrupted value is the `allowedSwapper[pool][sender]` extension decision: it evaluates `true` for the router address when it should evaluate `true` only for explicitly permitted end users.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address — this is the expected operational path. The trigger condition (router allowlisted) is therefore highly likely in any production deployment of an allowlisted pool that intends to support normal user flows. The attack requires no special privileges: any unprivileged user can call `router.exactInputSingle` with the target pool.

## Recommendation

The extension must validate the actual end user, not the intermediary. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Dedicated identity field or signed attestation in `extensionData`**: Add a field that the extension verifies, falling back to `sender` only when `sender` is not a known router.

At minimum, the `SwapAllowlistExtension` documentation must warn that allowlisting the router grants unrestricted access to all users, and the extension should not be used as a per-user gate when router-mediated swaps are expected.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — the only way to permit router swaps.
3. Non-allowlisted `attacker` calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router address.
5. Pool calls `extension.beforeSwap(router, ...)` — `sender` = router address.
6. Check: `allowedSwapper[pool][router]` = `true` → passes.
7. `attacker` successfully swaps on a pool they were never meant to access.

Foundry test outline:
- Deploy `SwapAllowlistExtension`, pool, and `MetricOmmSimpleRouter`.
- Admin calls `setAllowedToSwap(pool, address(router), true)`.
- Call `router.exactInputSingle` from an address that was never individually allowlisted.
- Assert the swap succeeds (no `NotAllowedToSwap` revert), confirming the bypass.