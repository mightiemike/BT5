Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract address, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the allowlist by routing through the router, completely defeating the access control mechanism.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the following check at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (used as the namespace key — correct), and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap` (lines 230–231 of `MetricOmmPool.sol`):

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
```

In `ExtensionCalling._beforeSwap` (lines 160–176), this value is forwarded verbatim as the first argument to `IMetricOmmExtensions.beforeSwap`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly (lines 72–80 of `MetricOmmSimpleRouter.sol`). The pool's `msg.sender` is therefore the **router address**, so the extension receives `sender = address(router)` and checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

The same misbinding occurs in `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165) — all four public swap entry points of the router call `pool.swap()` directly, making the router the `msg.sender` to the pool in every case.

**Exploit path:**
1. Admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading.
2. Admin allowlists the router: `setAllowedToSwap(pool, router, true)` — the natural operational step to enable router-mediated swaps.
3. Unprivileged attacker calls `router.exactInputSingle({pool: curated_pool, ...})`.
4. Router calls `pool.swap(attacker, ...)` → pool's `msg.sender` = router.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Attacker's swap executes; attacker receives output tokens.

The existing test `test_allowedSwapSucceeds` (lines 68–74 of `FullMetricExtension.t.sol`) only tests direct pool calls via `TestCaller`, not router-mediated calls, leaving this bypass untested.

## Impact Explanation
The allowlist is completely defeated. Every user who can call the router — i.e., the entire public — can trade on a pool designed to be restricted. LP funds in a curated pool are exposed to unrestricted arbitrage or toxic flow that the allowlist was meant to prevent. This constitutes broken core pool functionality (access control) with direct loss potential to LPs. Additionally, if the admin does not allowlist the router, legitimately allowlisted users cannot use the router at all (their swaps revert with `NotAllowedToSwap`), forcing them to call the pool directly and losing slippage protection, deadline enforcement, and multi-hop routing.

## Likelihood Explanation
The trigger is a standard, public, documented periphery call (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) — no special role or privileged access is required. Allowlisting the router is the expected operational step to make the router work with an allowlisted pool; the bug is latent in every such deployment. No malicious setup is required; the attacker only needs to call the public router with a valid pool address and token approval.

## Recommendation
The extension must gate the end user, not the intermediary. Two options:

1. **Pass the original caller through the router.** The router already stores `msg.sender` in transient storage for the payer context (via `_setNextCallbackContext`). Expose the original caller as a dedicated transient slot that the extension can read, and have the extension check that value instead of `sender`.

2. **Check `recipient` instead of `sender` in the extension.** The `recipient` is the address that receives output tokens and is set by the end user. The extension signature already receives `recipient` as the second argument (currently ignored). For a swap allowlist, gating `recipient` is semantically closer to "who benefits from this swap," though it does not prevent a non-allowlisted user from routing output to an allowlisted address.

The cleanest fix consistent with the existing architecture is option 1.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)   // to enable router path
  - Admin does NOT allowlist attacker

Attack:
  1. attacker calls router.exactInputSingle({
         pool: curated_pool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls pool.swap(attacker, true, X, ...)
     → msg.sender to pool = router
  3. Pool calls _beforeSwap(router, attacker, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router] == true → PASSES
  5. Swap executes; attacker receives output tokens

Result: Non-allowlisted attacker successfully swaps on a curated pool.

Foundry test: Add a test to FullMetricExtension.t.sol that:
  - Deploys the router
  - Allowlists the router via setAllowedToSwap(pool, router, true)
  - Has a non-allowlisted address call router.exactInputSingle targeting the curated pool
  - Asserts the swap succeeds (demonstrating the bypass)
```