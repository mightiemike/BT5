Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If a pool admin allowlists the router to enable router-based swaps for their curated users, any unprivileged user can bypass the per-user gate by routing through the router, completely neutralizing the allowlist for all router-mediated swaps.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the caller of the extension). `sender` is the first argument, which is set in `MetricOmmPool.swap` as:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    ...
```

When `MetricOmmSimpleRouter.exactInputSingle` executes, it calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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

The actual user's address (`msg.sender` of `exactInputSingle`) is stored only in transient storage via `_setNextCallbackContext` for the payment callback — it is never passed to the pool or to any extension. The pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`.

The same wrong-actor binding applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181). In the multi-hop `exactInput` case, intermediate hops use `address(this)` (the router itself) as the payer, making the problem even more pronounced.

No existing guard corrects this: the extension has no access to transient storage, no trusted-forwarder registry, and no mechanism to decode the real user from `extensionData` (which is passed through verbatim from the caller without any user-address injection by the router).

## Impact Explanation

A pool admin deploying a curated pool (KYC-only, institution-only, etc.) with `SwapAllowlistExtension` will naturally want to support the standard router. Allowlisting the router address so approved users can swap through it is the obvious and expected configuration step. This single admin action silently opens the pool to every user on-chain: any address can call `MetricOmmSimpleRouter.exactInputSingle` targeting the pool, the extension checks `allowedSwapper[pool][router]` → `true`, and the swap executes without any per-user check. The allowlist guard is completely neutralized for all router-mediated swaps. Non-allowlisted users gain full swap access to a pool designed to exclude them, undermining any regulatory, risk, or curation controls the pool was meant to enforce. This constitutes broken core pool functionality (access control) and an admin-boundary break reachable by an unprivileged path.

## Likelihood Explanation

This is a realistic, high-probability misconfiguration. Pool admins who configure a per-user allowlist will also want their users to benefit from the router's slippage protection, deadline checks, and multi-hop routing. Allowlisting the router is the obvious and expected step. Nothing in the extension's interface, NatDoc, or admin setter warns that doing so collapses the per-user gate. The bypass requires no special privilege, no flash loan, and no multi-block setup — any EOA can trigger it in a single transaction.

## Recommendation

The extension must check the economically relevant actor, not the immediate `msg.sender` of `pool.swap()`. Two sound approaches:

1. **Router passes actual user in `extensionData`**: Require the router to ABI-encode the originating user address as the first word of `extensionData` for allowlisted pools, and have the extension decode and check that address when `sender` is a known router.

2. **Trusted-forwarder registry**: Maintain a registry of trusted forwarder contracts; when `sender` is a forwarder, decode the real user from `extensionData`; otherwise check `sender` directly.

3. **Document the invariant clearly**: At minimum, document that allowlisting any shared contract (router, aggregator) collapses per-user gating to per-contract gating, and that pool admins must never allowlist shared intermediaries if individual-user control is required.

## Proof of Concept

```
Setup:
  Pool P configured with SwapAllowlistExtension E
  Pool admin allowlists alice (KYC'd) and the router R:
    E.setAllowedToSwap(P, alice, true)
    E.setAllowedToSwap(P, R, true)   ← intended to let alice use the router

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  router calls P.swap(recipient, ...) with msg.sender = R
  pool calls _beforeSwap(R, ...)
  extension checks allowedSwapper[P][R] → true
  swap executes — bob bypasses the per-user allowlist entirely

Result:
  bob swaps on a pool designed to exclude him.
  alice's KYC-only pool is now open to all users via the router.
```

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool with it, call `setAllowedToSwap(pool, router, true)`, then call `exactInputSingle` from an address not in the allowlist and assert the swap succeeds (no `NotAllowedToSwap` revert).