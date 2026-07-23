Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Original Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is used, `sender` is the router's address, not the original EOA. Once the router is allowlisted — operationally required for any allowlisted user to use the periphery — every user, including explicitly blocked ones, can bypass the per-user allowlist by routing through the public router contract.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the identity check at L37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct pool-identity binding), and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap()` at L230–231, the pool calls `_beforeSwap(msg.sender, ...)`, passing whoever called `pool.swap()` as `sender`. When `MetricOmmSimpleRouter.exactInputSingle` (L72–80) calls `pool.swap()`, the router is `msg.sender`, so `sender` in the extension equals `address(router)`.

The extension has no mechanism to recover the original EOA: `extensionData` is forwarded verbatim from the caller (`params.extensionData` at L79), and `SwapAllowlistExtension.beforeSwap` never decodes it. The same applies to `exactInput` (L104–112), `exactOutputSingle` (L136–137), and `exactOutput` (L165–181), all of which call `pool.swap()` with `msg.sender = router`.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension`, allowlists trusted user A, and also allowlists `address(router)` so user A can use the periphery.
2. Blocked user B calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
3. Router calls `pool.swap(...)` → pool calls `_beforeSwap(address(router), ...)`.
4. Extension evaluates `allowedSwapper[pool][address(router)]` → `true` → swap proceeds.
5. User B's swap executes at live oracle prices against pool liquidity with no allowlist enforcement.

## Impact Explanation
`SwapAllowlistExtension` is the production guard for pools restricting trading to specific counterparties (institutional LPs, whitelisted market makers, pools preventing public arbitrage during volatile oracle periods). Once the router is allowlisted — which is operationally necessary for any allowlisted user who wants to use the periphery — the guard is fully neutralized for all users. Unauthorized arbitrageurs can execute large swaps at live oracle prices, draining LP value in exactly the manner the allowlist was intended to prevent. This constitutes a direct loss of LP principal, meeting Sherlock Medium/High thresholds.

## Likelihood Explanation
The router is a public, permissionless contract. Any pool that wants allowlisted users to access the router must add `address(router)` to the allowlist; there is no other mechanism. Once the router is allowlisted, the bypass is trivially reachable by any EOA with no special privileges. The pool admin has no way to simultaneously allow specific users through the router and block others, because the extension cannot distinguish callers behind the same router address.

## Recommendation
The extension must gate on the original user, not the immediate pool caller. Two viable approaches:

1. **Router-forwarded identity in `extensionData`:** Standardize a convention where the router prepends `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool. `SwapAllowlistExtension.beforeSwap` decodes the first word as the claimed original sender and verifies it. This requires a trusted router (already the case in this architecture).

2. **Transient-storage identity propagation:** The router writes the original `msg.sender` into a well-known transient slot before calling `pool.swap()`. The extension reads that slot directly. This avoids any `extensionData` encoding convention.

Either approach must be applied consistently across all `exact*` router entry points.

## Proof of Concept
```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
ext.setAllowedToSwap(pool, trustedUser, true);
ext.setAllowedToSwap(pool, address(router), true); // ← necessary for periphery use

// Attack: blockedUser routes through the public router
vm.startPrank(blockedUser);
token0.approve(address(router), type(uint256).max);
// pool.swap() sees msg.sender = router → allowedSwapper[pool][router] = true → passes
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: blockedUser,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        tokenIn: token0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// blockedUser successfully swapped — allowlist completely bypassed
```