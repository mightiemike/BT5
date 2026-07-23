Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` mediates a swap, `sender` is the router address, not the end-user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every user unrestricted swap access through the router, completely defeating the per-user allowlist guard.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // <-- direct caller of swap(), i.e. the router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this `sender` unchanged to the extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))`.

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check is `allowedSwapper[pool][router]`.

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,   // end-user as recipient
    params.zeroForOne,
    ...
);
```

The router is `msg.sender` inside the pool, so `sender` forwarded to `beforeSwap` is the router address. The end-user (`params.recipient`) is passed as the second argument (`recipient`) but the extension ignores it (the `address` parameter is unnamed/discarded at L31).

The pool admin faces an impossible choice: not allowlisting the router makes all router-mediated swaps revert; allowlisting the router passes the check for every user who routes through it, since the check is on the router address, not the individual user.

No existing guard compensates for this: there is no `isKnownRouter` mapping, no fallback to `recipient`, and no mechanism in `extensionData` that the extension uses to recover the originating user.

## Impact Explanation
Any user can swap on a pool intended to be restricted to a specific allowlist by routing through `MetricOmmSimpleRouter`. Once the router is allowlisted (required for router-mediated swaps to function), the allowlist provides zero per-user protection. Pools designed to serve only trusted or compliance-restricted counterparties are fully open to the public via the router. Unauthorized swaps against an oracle-priced pool can drain LP inventory at oracle-fair prices, causing direct loss of LP principal. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break by an unprivileged path" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary documented user-facing swap entry point. Any pool admin deploying `SwapAllowlistExtension` who also wants users to use the standard router will naturally allowlist the router address. The bypass requires no exploit setup — it is the default user path. Any unprivileged user can repeat this on every affected pool.

## Recommendation
Option 1 (simplest): Check `recipient` when `sender` is a known router, since the router passes the end-user as `recipient` for single-hop swaps:

```solidity
function beforeSwap(address sender, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    address subject = isKnownRouter[sender] ? recipient : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][subject]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Option 2: Require the router to encode the originating user in `extensionData` and have the extension decode and verify it.

Option 3: Document that `SwapAllowlistExtension` gates the direct caller only and that pools using it must never allowlist any intermediary contract including the router.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension wired to beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(bob_as_recipient, ...)           // msg.sender inside pool = router
  - Pool calls _beforeSwap(sender=router, recipient=bob, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes despite not being on the allowlist

Foundry test plan:
  1. Deploy pool + SwapAllowlistExtension, wire beforeSwap order.
  2. setAllowedToSwap(pool, router, true); setAllowedToSwap(pool, alice, true).
  3. vm.prank(bob); router.exactInputSingle(..., recipient=bob, ...).
  4. Assert swap succeeds (no NotAllowedToSwap revert).
  5. Assert bob received output tokens despite never being allowlisted.
```