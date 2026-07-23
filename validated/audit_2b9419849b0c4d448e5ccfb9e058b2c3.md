Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual user on router-mediated swaps, enabling full allowlist bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. If the pool admin allowlists the router (the only way to let allowlisted users use the standard periphery path), every unprivileged user can bypass the curated-pool gate by routing through the router, since `allowedSwapper[pool][router]` returns `true` for any caller.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` (L163-165). `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the pool's `msg.sender`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `IMetricOmmPoolActions(params.pool).swap(...)` directly (L72-80), making the router the pool's `msg.sender`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Bypass path**: A pool admin who wants allowlisted users to use the router must call `setAllowedToSwap(pool, router, true)`. Once set, `allowedSwapper[pool][router] == true` for every caller, so any unprivileged user can swap on the curated pool by routing through the router. The same applies to every hop in `exactInput` (L103-112) and every recursive step in `exactOutput` via `_exactOutputIterateCallback` (L220-228).

**Broken-functionality path**: If the admin does not allowlist the router, every allowlisted user is silently blocked from using the standard periphery path.

## Impact Explanation
Once the router is allowlisted, any address gains full swap access to the curated pool, defeating KYC/institutional gating. Unauthorized users can drain LP principal at oracle-derived prices. This is a complete failure of the configured access-control invariant and constitutes a direct loss of LP principal — a Critical/High impact under the allowed impact gate (broken core pool functionality causing loss of funds; admin-boundary break bypassed by an unprivileged path).

## Likelihood Explanation
The trigger is a single, operationally motivated admin action: allowlisting the router so that allowlisted users can use the standard periphery path. The admin has no on-chain signal that this opens the pool to everyone. Once the router is allowlisted, the bypass is reachable by any unprivileged user with zero additional privilege, zero cost, and is repeatable indefinitely. Likelihood is medium (requires the admin to take the natural remediation step) with high impact once triggered.

## Recommendation
The extension must check the actual economic actor, not the immediate pool caller. Two options:

1. **Router-forwarded identity**: Have the router ABI-encode the real user address into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router.
2. **Recipient-based check**: Gate on `recipient` (the address that receives tokens) rather than `sender`, since the recipient is always the intended beneficiary and cannot be spoofed by an intermediary.

Option 1 is more general and preserves sender-gating semantics; option 2 is simpler but changes the gating axis.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension; pool admin allowlists alice:
       extension.setAllowedToSwap(pool, alice, true)

2. Pool admin also allowlists the router so alice can use it:
       extension.setAllowedToSwap(pool, router, true)

3. Unprivileged user charlie (NOT allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: charlie, ...})

4. Router calls pool.swap(...); pool calls extension.beforeSwap(router, ...)
       allowedSwapper[pool][router] == true  →  check passes

5. Charlie's swap executes on the curated pool — allowlist fully bypassed.
```