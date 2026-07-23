All cited code is confirmed in the repository. The call chain is verified:

1. `MetricOmmPool.sol` L231: `_beforeSwap(msg.sender, ...)` — passes the direct caller (router) as `sender`.
2. `ExtensionCalling.sol` L149-177: `_beforeSwap` forwards `sender` unchanged to `beforeSwap`.
3. `SwapAllowlistExtension.sol` L37: checks `allowedSwapper[msg.sender][sender]` — gates on the router address, not the actual user.
4. `DepositAllowlistExtension.sol` L38: checks `allowedDepositor[msg.sender][owner]` — correctly gates on the actual beneficiary.
5. `MetricOmmSimpleRouter.sol` L72-80: router calls `pool.swap()` directly, making itself `msg.sender` at the pool level.

The structural inconsistency is real and code-confirmed. The bypass requires the pool admin to allowlist the router address — a plausible misconfiguration given that `DepositAllowlistExtension` correctly handles the intermediary-caller pattern (checking `owner`), leading an admin to reasonably expect the same behavior from `SwapAllowlistExtension`.

---

Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `sender`, which the pool sets to `msg.sender` of `swap()` — the router contract, not the actual user. If a pool admin allowlists the router address (a plausible step to let allowlisted users access the router), any unprivileged user can bypass the swap allowlist by routing through `MetricOmmSimpleRouter`. This is structurally inconsistent with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks `owner` (the actual beneficiary) rather than `sender` (the direct caller).

## Finding Description
**Root cause — `SwapAllowlistExtension.beforeSwap`:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` at L231, passing the direct caller as `sender`. `ExtensionCalling._beforeSwap` (L149-177) forwards this unchanged. `SwapAllowlistExtension.beforeSwap` (L37) then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly (L72-80), making the pool's `msg.sender` the router. So `sender = router`, and `allowedSwapper[pool][router]` is what gets checked — not the actual user.

**Contrast with `DepositAllowlistExtension`:**

`beforeAddLiquidity(address, address owner, ...)` ignores `sender` and checks `allowedDepositor[msg.sender][owner]` (L38) — the actual beneficiary. Even when `MetricOmmPoolLiquidityAdder` is the direct caller, the allowlist correctly gates the actual depositor. The swap extension has no equivalent mechanism.

**Exploit path:**
```
User (not allowlisted) → MetricOmmSimpleRouter.exactInputSingle()
    → pool.swap()  [pool's msg.sender = router]
        → _beforeSwap(sender = router, ...)
            → SwapAllowlistExtension.beforeSwap(sender = router, ...)
                → allowedSwapper[pool][router] == true → passes
```

## Impact Explanation
A pool admin who configures a swap allowlist to restrict trading to specific addresses (e.g., KYC'd market makers) and also allowlists the router address inadvertently opens the pool to all users. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput` and execute swaps on the restricted pool. If the pool is configured with favorable pricing for specific market makers, unauthorized traders can extract value from LPs through arbitrage or adverse selection, causing direct LP principal loss. This meets the "direct loss of user principal" and "broken core pool functionality" impact criteria.

## Likelihood Explanation
The trigger is plausible: (1) the pool admin wants allowlisted users to use the router for multi-hop or slippage-protected swaps; (2) the admin allowlists the router address, not realizing this is equivalent to `allowAllSwappers = true` for router-mediated paths; (3) the inconsistency with `DepositAllowlistExtension` (which correctly checks `owner` through an intermediary caller) makes this mistake easy to make — an admin who observed the deposit allowlist correctly gating the actual user through the adder would reasonably expect the swap allowlist to behave the same way; (4) no code-level warning or NatSpec in `SwapAllowlistExtension` flags this difference.

## Recommendation
Change `SwapAllowlistExtension.beforeSwap` to check a caller-supplied identity from `extensionData` rather than `sender`, or add a secondary check that also validates the `recipient` or a user-signed identity. Alternatively, align the swap allowlist with the deposit allowlist pattern by having the router forward the actual user identity through `extensionData` and having the extension decode and check it. At minimum, add a NatSpec warning that allowlisting the router address grants access to all users.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow allowlisted users to use the router.
3. Non-allowlisted `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Pool receives `swap()` with `msg.sender = router`.
5. `_beforeSwap(sender = router, ...)` is dispatched.
6. `SwapAllowlistExtension` checks `allowedSwapper[pool][router] == true` → passes.
7. Attacker's swap executes on the restricted pool despite not being on the allowlist.