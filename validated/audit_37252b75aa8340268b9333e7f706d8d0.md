Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for legitimate users), the check passes for every caller regardless of individual allowlist status, completely defeating the access control guard.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,  // whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes and dispatches this `sender` verbatim to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

At this point `msg.sender` to the pool is the router contract. The `sender` forwarded to `beforeSwap` is therefore the router address, not the end user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

Two harmful outcomes result:
1. **Bypass (primary):** The pool admin must allowlist the router to permit any router-mediated swap for legitimate users. Once `allowedSwapper[pool][router] == true`, the check passes for every caller. Any non-allowlisted user bypasses the guard by calling through the router.
2. **Broken functionality (secondary):** If the router is not allowlisted, every allowlisted user who tries to swap through the router is rejected with `NotAllowedToSwap`, making the standard periphery path unusable.

No existing guard prevents this. The `extensionData` field is passed through but `SwapAllowlistExtension` does not read it. There is no mechanism in the pool or router to propagate the originating user's address to the extension check.

## Impact Explanation
The swap allowlist is the primary access-control mechanism for restricting who may trade in a pool (KYC-gated pools, institutional-only pools, beta-access pools). When the bypass is active, any address can execute swaps against the pool's liquidity, draining LP value through unrestricted trading that the pool admin explicitly intended to prevent. This constitutes broken core pool functionality causing loss of funds and unusable swap flows, matching the allowed impact gate.

## Likelihood Explanation
The router is the canonical user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address — this is the expected operational path. The bypass is therefore reachable in any realistic deployment where the pool admin has enabled router access, which is the common case. The attack requires no special privileges: any unprivileged address can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router.

## Recommendation
The extension must gate on the economically relevant actor — the end user — not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`:** Have the router encode `msg.sender` (the end user) into `extensionData` before calling `pool.swap`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of `sender`.

2. **Fallback to `extensionData`-encoded user when `sender` is a known router:** The extension reads a user address from `extensionData` when `sender` is a registered router, and checks `sender` directly when it is not.

Either way, the allowlist lookup must resolve to the address that controls the economic decision to swap, not the contract that mechanically forwards the call.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is allowlisted)
  allowedSwapper[pool][bob]   = false  (bob is NOT allowlisted)
  allowedSwapper[pool][router] = true  (admin allowlists router so alice can use it)

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)          [MetricOmmSimpleRouter.sol L72-80]
  → pool calls _beforeSwap(msg.sender=router, ...)  [MetricOmmPool.sol L230-240]
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → allowedSwapper[pool][router] == true  ✓
  → swap proceeds — bob bypasses the allowlist
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` configured.
2. Set `allowedSwapper[pool][alice] = true`, `allowedSwapper[pool][router] = true`.
3. Call `router.exactInputSingle` from `bob` (non-allowlisted).
4. Assert swap succeeds — demonstrating the bypass.
5. Remove router from allowlist; assert `bob`'s direct `pool.swap()` call reverts with `NotAllowedToSwap`.