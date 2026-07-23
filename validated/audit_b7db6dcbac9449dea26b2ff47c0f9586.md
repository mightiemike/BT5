Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the originating user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access on a pool. It checks the `sender` argument forwarded by the pool, which is always `msg.sender` of the `pool.swap(...)` call. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router — the only way to let approved users reach the pool through the periphery — every public caller of the router inherits the router's allowlisted status and can bypass the gate entirely.

## Finding Description

**Root cause — pool passes `msg.sender` verbatim as `sender`:**

In `MetricOmmPool.sol` at line 230–240, `_beforeSwap` is called with `msg.sender` as the `sender` argument:

```solidity
_beforeSwap(
    msg.sender,   // always the direct caller of pool.swap
    recipient, zeroForOne, amountSpecified, priceLimitX64,
    packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

**Root cause — extension checks that value against the allowlist:**

`SwapAllowlistExtension.beforeSwap` (line 37) checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (used as the mapping key, correct), and `sender` is the address the pool passed — which is the router when routing through `MetricOmmSimpleRouter`.

**Root cause — router never forwards the originating user as `sender`:**

`MetricOmmSimpleRouter.exactInputSingle` (lines 71–80) stores the original `msg.sender` only in transient storage for token settlement, then calls `pool.swap(...)` directly:

```solidity
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

The originating user's address is never passed to the pool as the `sender` identity. The same pattern applies to `exactOutputSingle` (line 135–137) and `exactInput` (line 103–112).

**Exploit path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension`, sets `allowAllSwappers[pool] = false`, and allowlists a set of approved users via `setAllowedToSwap(pool, approvedUser, true)`.
2. To let those approved users reach the pool through the periphery, the admin calls `setAllowedToSwap(pool, router, true)`.
3. Any unapproved user calls `router.exactInputSingle(...)` targeting the restricted pool.
4. The router calls `pool.swap(...)` — the pool's `msg.sender` is the router.
5. The pool calls `_beforeSwap(sender=router, ...)`. The extension checks `allowedSwapper[pool][router]` → `true` → passes.
6. The unapproved user's swap executes against the restricted pool.

**Existing guards are insufficient:** There is no mechanism in the extension, the pool, or the router to propagate the originating user identity to the extension check. The `extensionData` field is user-supplied and unauthenticated, so it cannot be trusted for identity.

## Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access on a pool. Once bypassed, every public caller of `MetricOmmSimpleRouter` can execute swaps that the pool admin explicitly intended to block. This breaks the core access-control invariant of the extension and constitutes broken core pool functionality: the allowlist gate is rendered entirely ineffective for all router-mediated swaps. Any pool relying on this extension for access control (e.g., KYC-gated pools, institutional pools, or pools with restricted counterparties) is fully exposed to unrestricted swap access by any address.

## Likelihood Explanation

The bypass requires the router to be allowlisted. This is the natural and expected configuration: without it, even approved users cannot reach the pool through the standard periphery router, making the allowlist operationally unusable for any real deployment that relies on the standard periphery. A pool admin reading the `SwapAllowlistExtension` interface in isolation has no indication that allowlisting the router grants access to all router users rather than only the approved subset. The likelihood of this misconfiguration is high for any pool that uses both the allowlist extension and the standard router.

## Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary contract. Two viable approaches:

1. **Pass originating user in `extensionData` with router authentication:** Have the router encode `msg.sender` into `extensionData` and have the extension verify that the call came from a trusted router before trusting the encoded identity. This requires a coordinated convention between router and extension, and the extension must maintain a registry of trusted routers.
2. **Reject router-mediated calls unless extension data carries authenticated user identity:** The extension can detect router-mediated calls (e.g., by checking whether `sender` is a known router) and revert unless the extension data carries a verifiable user identity signed by a trusted authority.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin: setAllowAllSwappers(pool, false)
// 3. Pool admin: setAllowedToSwap(pool, approvedUser, true)
// 4. Pool admin: setAllowedToSwap(pool, router, true)  // natural config for periphery access
// 5. Unprivileged attacker calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// pool._beforeSwap(sender=router) → allowedSwapper[pool][router]=true → passes
// Attacker swap executes on restricted pool
```