Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool receives the router contract as `sender`, not the end user. A pool admin who allowlists the router address — the only practical way to let allowlisted users trade through the standard periphery — inadvertently opens the gate to every user who calls the router, completely defeating the allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    ...
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

At this point `msg.sender` of `pool.swap()` is the **router contract**, so `sender` seen by the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`.

**The trap:** A pool admin who wants allowlisted users to trade through the standard periphery must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call arriving through the router — regardless of who the actual end user is. Any non-allowlisted address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the guard passes.

The same problem applies to the multi-hop `exactInput` path, where the router is `msg.sender` for every pool hop:

```solidity
// MetricOmmSimpleRouter.sol L103-112
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        ...
    );
```

There is no mechanism in the router to forward the originating user's identity to the pool or to the extension.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist becomes a direct-call-only gate that the standard periphery silently bypasses. This results in unauthorized users executing swaps and extracting value from LP positions that were priced for a restricted counterparty set, constituting a direct loss of LP assets and complete failure of the curation invariant the pool admin configured. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact criteria.

## Likelihood Explanation
The scenario is reachable by any unprivileged user with no special setup. The only precondition is that the pool admin has allowlisted the router — a natural and expected action for any pool that wants its allowlisted users to trade through the standard periphery rather than calling the pool directly. `MetricOmmSimpleRouter` is a public, permissionless contract. No privileged access, malicious token, or non-standard ERC-20 is required. The attack is repeatable indefinitely.

## Recommendation
The extension must gate the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through the router.** The router should encode the real `msg.sender` into `extensionData` (or a dedicated field) and the extension should decode and verify it. This requires a trust assumption that the router is the only allowed intermediary, which can be enforced by checking `sender == trustedRouter` before accepting the decoded identity.

2. **Check `tx.origin` as a fallback only if `sender` is a trusted router.** If `sender` is a known trusted router, the extension can fall back to `tx.origin` to identify the originating EOA. This is safe only when the router is explicitly trusted and the pool does not need to support contract-initiated swaps.

3. **Document that the allowlist only gates direct pool calls.** If router bypass is acceptable, the `SwapAllowlistExtension` NatSpec and pool admin tooling must clearly state that routing through `MetricOmmSimpleRouter` is not gated.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can trade through the standard periphery.
3. Non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` with `msg.sender = router`.
5. Pool calls `extension.beforeSwap(router, ...)` via `_beforeSwap`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Attacker's swap executes against the curated pool with no allowlist enforcement.

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist only the router and one legitimate user, then call `exactInputSingle` from an address not in the allowlist and assert the swap succeeds (demonstrating the bypass).