Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the router contract — not the originating user. When a pool admin allowlists the router to permit router-mediated swaps for legitimate users, every public caller of the router automatically passes the gate, defeating the allowlist entirely and allowing unauthorized users to swap against restricted pools.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← the router when called via MetricOmmSimpleRouter
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension via `abi.encodeCall`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)`. The pool's `msg.sender` is the router address, so `sender` arriving at the extension is the router's address, not the user's address. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The router does store the real payer in transient storage (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)` at `MetricOmmSimpleRouter.sol` L71), but this information is never forwarded to the extension — neither through `sender` nor through `extensionData`. The pool has no mechanism to expose the transient payer to extensions.

The existing test suite (`FullMetricExtension.t.sol`) only tests direct pool calls via `TestCaller` contracts, never testing the router-mediated path, so the bypass is undetected.

## Impact Explanation
A pool admin who wants to restrict swaps to a specific set of users (e.g., KYC'd counterparties, whitelisted market makers) deploys the pool with `SwapAllowlistExtension`. To let those users interact through the standard `MetricOmmSimpleRouter`, the admin must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, the gate is open to every public caller of the router — any address can call `exactInputSingle`, `exactInput`, or `exactOutputSingle` and the extension will pass them because it sees the router, not the user. Unauthorized users can drain LP positions at oracle-quoted prices, causing direct loss of LP principal. This is a High severity direct loss of user principal impact.

## Likelihood Explanation
The router is the primary user-facing entry point documented and expected by the protocol. Any pool admin who configures a swap allowlist and also wants their allowlisted users to use the router will inevitably allowlist the router, triggering the bypass. The attacker needs no special privilege: a single public call to `MetricOmmSimpleRouter.exactInputSingle` suffices. The condition is reachable on every allowlisted pool that also permits router access.

## Recommendation
The extension must check the economically relevant actor — the end user — not the intermediary. Two complementary approaches:

1. **Pass the original initiator through the hook.** The pool could forward an additional `initiator` field (the address that originally called the router, recoverable from transient storage the router already writes) alongside `sender`. The extension would then gate on `initiator`.

2. **Require the router to encode the real user in `extensionData`.** The router would encode `msg.sender` into `extensionData`, and the extension would decode and check it when `sender` is a known router address.

The simplest safe fix is option 1: store the real user in transient storage at the router entry point and expose it to extensions via a dedicated slot that the pool reads and forwards.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only allowed user
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: token0, ...})

  Execution path:
    router.exactInputSingle()
      → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
          msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              allowedSwapper[pool][router] == true  ✓  (passes!)
        → swap executes, bob receives token1 output

Result: bob, who is not on the allowlist, successfully swaps against the restricted pool.

Foundry test plan:
  1. Deploy pool with SwapAllowlistExtension
  2. setAllowedToSwap(pool, alice, true)
  3. setAllowedToSwap(pool, router, true)
  4. vm.prank(bob); router.exactInputSingle(...)
  5. Assert swap succeeds (no revert) — demonstrating bypass
```