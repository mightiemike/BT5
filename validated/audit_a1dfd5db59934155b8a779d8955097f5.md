Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any unprivileged user to bypass the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router's address rather than the actual end-user. If the router is allowlisted for a pool — the only way to permit router-mediated swaps — every unprivileged user can bypass the allowlist by routing through the public router contract.

## Finding Description
In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check becomes `allowedSwapper[pool][router]`. The pool admin faces an impossible choice: allowlisting the router opens the pool to every user on the internet; not allowlisting it prevents allowlisted users from using the router at all. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the real caller's identity to the extension, and `extensionData` is user-controlled and cannot be trusted for identity.

## Impact Explanation
Any pool configured with `SwapAllowlistExtension` for access control (regulatory compliance, KYC gating, market-maker-only pools) can be fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The attacker can execute swaps on a pool intended to be closed to them, drain liquidity from restricted pools at oracle-anchored prices, and manipulate pool state (bin position, tick) in ways the allowlist was designed to prevent. This is a direct loss of the access-control invariant with fund-impacting consequences for LPs and the protocol, meeting the admin-boundary break and broken core pool functionality criteria.

## Likelihood Explanation
Likelihood is high. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it with any pool address. No privileged setup is required beyond the pool admin having deployed the pool with `SwapAllowlistExtension` and allowlisted the router (the only way to support router-mediated swaps). The attacker needs no special role, no flash loan, and no frontrunning — a single `exactInputSingle` call suffices.

## Recommendation
The extension must gate the real end-user, not the intermediary. Two sound approaches:

1. **Router-level forwarding with pool verification**: Have the router encode `msg.sender` into a dedicated field, and have the pool expose it as a separate `realSender` parameter to extensions. The pool (not the user) would set this field, making it unforgeable.
2. **Extension reads transient context from the router**: The router writes the real caller into transient storage before calling the pool; the extension reads it from the router's known address. This requires a trusted router registry in the extension.

Until fixed, pools relying on `SwapAllowlistExtension` for access control should not allowlist the router, accepting that allowlisted users must call the pool directly.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin sets allowedSwapper[pool][router] = true  (to support router swaps).
3. Pool admin sets allowedSwapper[pool][alice] = true   (intended allowlisted user).
4. Pool admin does NOT set allowedSwapper[pool][bob] = true (bob is blocked).

5. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
6. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData).
   → msg.sender of pool.swap() = router
7. Pool calls _beforeSwap(router, ...).
8. Extension evaluates: allowedSwapper[pool][router] == true → PASSES.
9. Bob's swap executes successfully on a pool he was supposed to be blocked from.
```