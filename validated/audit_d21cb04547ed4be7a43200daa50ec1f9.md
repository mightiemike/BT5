Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` that `MetricOmmPool.swap()` receives. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router to permit router-mediated swaps for their approved users inadvertently opens the allowlist to every unprivileged caller, because the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is the caller, `sender` is the router address, not the end user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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

The router stores `msg.sender` only in the callback context (for payment), not in any field forwarded to the extension. The extension receives `sender = router` for every user who calls through the router. The same flaw exists in the multi-hop `exactInput` path (line 103-112), where the router is `msg.sender` for every pool hop.

**Exploit path:**
1. Admin deploys pool with `SwapAllowlistExtension`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — alice is approved.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary for alice to use the router, since the pool sees the router as the swapper.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient=bob, ...)`.
6. Pool calls `_beforeSwap(msg.sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob trades against LP funds with no revert.

No existing guard prevents this. The extension has no mechanism to recover the original caller's identity from `sender`, and the router provides no authenticated originator field to extensions.

## Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-guarded pool intends to restrict trading to a known set of counterparties (KYC-verified addresses, institutional partners, whitelisted bots). Once the router is allowlisted — a necessary operational step to support router-mediated swaps for approved users — the guard collapses entirely. Any unprivileged user can trade against LP funds that were never intended to be accessible to them. This constitutes a direct loss of LP principal: pool liquidity is consumed by parties the access control was explicitly designed to exclude. This matches the "Allowlist path: deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router" Smart Audit Pivot and the "Critical/High/Medium direct loss of user principal or owed LP assets" allowed impact gate.

## Likelihood Explanation

The only precondition is that the pool admin has allowlisted the router, which is a natural and expected operational step for any allowlist-gated pool that also wants to support the public router for its approved users. The router is a public, permissionless contract. No malicious setup, non-standard tokens, or privileged access is required by the attacker. The attack is repeatable by any address with no special capabilities.

## Recommendation

The pool should propagate the original caller's identity to extensions as a distinct field, separate from the immediate `msg.sender`. Two complementary fixes:

1. **Protocol-level (preferred):** Add an `originator` field to the `_beforeSwap` / `beforeSwap` hook signature. `MetricOmmPool.swap()` writes `msg.sender` to transient storage at entry and passes it as `originator` to all extension hooks. The router can optionally override this via a dedicated transient slot before calling `pool.swap()`, authenticated by the pool reading only from its own transient storage.

2. **Extension-side:** `SwapAllowlistExtension` accepts an authenticated `extensionData` payload carrying the verified original caller, signed or committed by the router in a way the extension can verify (e.g., the router writes the caller to a known transient slot that the extension reads directly from the router contract).

Without a protocol-level mechanism, `SwapAllowlistExtension` cannot safely recover the true user from `sender` alone when a router is in the call path.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is approved
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
  - Pool admin does NOT call setAllowedToSwap(pool, bob, true) // bob is NOT approved

Attack:
  - bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=bob, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap proceeds; bob trades against LP funds
  - Allowlist guard is fully bypassed with no revert

Foundry test outline:
  1. deployPool(extensions=[swapAllowlist])
  2. swapAllowlist.setAllowedToSwap(pool, alice, true)
  3. swapAllowlist.setAllowedToSwap(pool, address(router), true)
  4. vm.prank(bob); router.exactInputSingle(ExactInputSingleParams({pool: pool, recipient: bob, ...}))
  5. assertEq(swap succeeded, true)  // bob bypassed allowlist
```