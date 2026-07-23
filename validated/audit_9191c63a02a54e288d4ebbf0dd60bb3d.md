Audit Report

## Title
Swap Allowlist Bypassed via Router: `sender` Identity Lost When Pool Is Called Through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted (required for any router-based swap to succeed on a restricted pool), every user — including those explicitly excluded — can bypass the per-user allowlist by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever the pool passes as the first argument to `_beforeSwap`. In `MetricOmmPool.swap()`, `msg.sender` of the pool call is passed as `sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this is the router when called via router
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L71-80
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

The pool's `msg.sender` is the router. The actual user's address (`msg.sender` of the router call) is stored only in transient storage via `_setNextCallbackContext` and is never forwarded to the pool or the extension. The extension therefore sees `sender = router`, not the actual user.

**The bypass**: A pool admin who wants to restrict swaps to a specific set of users configures `SwapAllowlistExtension` and allowlists individual addresses. To allow those users to also use the router, the admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check `!allowedSwapper[msg.sender][sender]` passes for every user who routes through the router — including those explicitly excluded from the allowlist.

## Impact Explanation
The swap allowlist is the primary access-control mechanism for restricted pools (e.g., KYC-gated, compliance-restricted, or institutional-only pools). Any non-allowlisted user can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` on a pool where the router is allowlisted. The pool admin cannot simultaneously allow router-based swaps for legitimate users and block non-allowlisted users from using the router — the two goals are mutually exclusive under the current design. Unauthorized users execute swaps on a pool that was intended to be restricted, violating the pool's access-control invariant. This constitutes broken core pool functionality causing unauthorized swap execution, directly matching the allowed impact gate for broken core pool functionality.

## Likelihood Explanation
- The router is a public, permissionless contract. Any user can call it.
- For a restricted pool to be usable at all via the router, the admin must allowlist the router. This is the expected operational setup.
- Once the router is allowlisted, the bypass requires zero special privileges: any user calls `exactInputSingle` with the restricted pool address.
- The bypass is reachable in one transaction with no preconditions beyond the router being allowlisted.

## Recommendation
The extension must gate the **originating user**, not the immediate caller of the pool. The cleanest fix is to pass the original user through `extensionData`: the router encodes `msg.sender` into `extensionData` before calling `pool.swap()`, and `SwapAllowlistExtension.beforeSwap` reads the originator from `extensionData` when present, falling back to `sender` for direct pool calls. This avoids coupling the extension to the router and preserves backward compatibility for direct pool callers.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists `alice` (a legitimate user) and the router address via `setAllowedToSwap`.
3. Pool admin does **not** allowlist `bob` (an unauthorized user).
4. `bob` calls `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool.
5. The router calls `pool.swap(...)` — pool's `msg.sender` is the router.
6. The pool calls `_beforeSwap(msg.sender=router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `bob` successfully swaps on a pool he was explicitly excluded from.

The allowlist check that should have blocked `bob` at step 7 instead passes because the router's address — not `bob`'s — is the `sender` the extension sees.