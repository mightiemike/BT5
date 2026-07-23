Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the identity to gate, but when a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the router contract, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the public internet can bypass the allowlist by calling through the router, executing swaps against a pool intended to be access-restricted.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes that same `sender` into the call to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified, ...))
);
```

`SwapAllowlistExtension.beforeSwap` checks whether that `sender` value is on the allowlist, using `msg.sender` (the pool) as the pool key and `sender` (the router) as the identity:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly — it does **not** encode the original `msg.sender` into `extensionData`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The pool therefore passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]` — not the end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps), the check passes for every caller regardless of their individual allowlist status. There is no configuration that correctly restricts individual users while still permitting router-mediated swaps.

## Impact Explanation

`SwapAllowlistExtension` is the primary mechanism for restricting swap access to a pool (KYC-gated pools, private institutional pools). When the bypass is active, any unprivileged user can execute swaps against a pool intended to be restricted. Swaps drain LP token balances at oracle-derived prices; an attacker who can force swaps in either direction can extract value from LP positions. This constitutes direct loss of LP principal and breaks the core pool invariant that only authorized actors may trade. This meets the "Broken core pool functionality causing loss of funds" and "Admin-boundary break" criteria.

## Likelihood Explanation

The bypass is reachable by any user who knows the pool address and the public router address. The only precondition is that the pool admin has allowlisted the router — a natural and necessary step when deploying a pool intended to be usable via the standard periphery. No privileged access, no special token behavior, and no malicious setup is required beyond the normal deployment flow. The attack is repeatable and requires no special on-chain state beyond the router being allowlisted.

## Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Check `recipient` rather than `sender`**: For swap allowlists, gate on `recipient` (the address receiving value) rather than `sender` (the address initiating the call), since the recipient is the economically benefiting party and cannot be spoofed by the router.

Additionally, `DepositAllowlistExtension` should be audited for the symmetric issue on the `beforeAddLiquidity` path, where `sender` is the `MetricOmmPoolLiquidityAdder` rather than the depositing user.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured; set `allowAll = false`.
2. Allowlist only `alice` and the `MetricOmmSimpleRouter` address (required for router functionality).
3. As `charlie` (not allowlisted), call `router.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(...)` — pool's `msg.sender` is the router.
5. The pool calls `_beforeSwap(sender = router, ...)` (confirmed at `MetricOmmPool.sol` L231).
6. `ExtensionCalling._beforeSwap` encodes `sender = router` into the extension call (confirmed at `ExtensionCalling.sol` L165).
7. `SwapAllowlistExtension` checks `allowedSwapper[pool][router]` → `true` (confirmed at `SwapAllowlistExtension.sol` L37).
8. The swap executes. Charlie has successfully traded on a pool that was supposed to block him, draining LP funds at oracle prices.