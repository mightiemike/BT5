Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool populates with `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of that call, so the extension checks the router's address against the per-pool allowlist rather than the actual user's address. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the pool to every address on the network.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value as `sender` to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` checks that `sender` against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` executes, it calls the pool directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

The router is `msg.sender` of this `pool.swap()` call. Therefore `sender` arriving at the extension is `address(router)`, never the real user. The same pattern holds for `exactOutputSingle` (L136-137), `exactInput` (L104-112), and every recursive hop of `exactOutput` (L165-181 and L220-228).

The existing test suite only exercises `TestCaller` contracts that call the pool directly (`swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true)` then `_swap(0, users[0], ...)`), so the router-mediated path is never tested.

## Impact Explanation

**Outcome A — Full allowlist bypass (High):** A pool admin who wants allowlisted users to reach the pool through the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every address on the network can call `router.exactInputSingle` and swap in the curated pool, because the extension only checks whether the router is allowed. The allowlist is completely defeated — any non-allowlisted address can drain the pool's liquidity at oracle prices.

**Outcome B — Allowlisted users permanently blocked from the router (Medium):** If the admin allowlists individual users but not the router, those users cannot use `MetricOmmSimpleRouter` at all — every router-mediated swap reverts with `NotAllowedToSwap`. The primary documented periphery entrypoint is unusable for the intended participants.

Both outcomes break the core invariant that a curated pool enforces the same access policy regardless of which supported public entrypoint reaches it.

## Likelihood Explanation

Likelihood is high. `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint in the periphery. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and expects users to interact through the router will encounter this immediately. No special permissions, flash loans, or unusual token behavior are required — a standard `exactInputSingle` call is sufficient to trigger the bypass.

## Recommendation

Pass the economically relevant actor through the pool's `swap` call so the extension can check it. Two concrete approaches:

1. **Explicit `payer` parameter:** Add a `payer` (or `originator`) field to the pool's `swap` signature. The router sets it to `msg.sender` before forwarding to the pool. The pool passes it to `_beforeSwap` alongside `recipient`. The extension checks `payer` instead of `sender`. This is preferred because it makes the actor binding explicit and verifiable at the protocol level.

2. **Extension-data forwarding:** The router encodes `msg.sender` into `extensionData` and the extension decodes and verifies it. This requires the extension to trust the router's encoding, which is weaker than option 1.

## Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   (necessary so that allowlisted users can reach the pool through the router).
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).
   attacker is explicitly not allowlisted.

Attack
──────
4. attacker calls:
     router.exactInputSingle(ExactInputSingleParams{
       pool:       pool,
       recipient:  attacker,
       zeroForOne: false,
       amountIn:   X,
       ...
     })

5. Router executes:
     pool.swap(attacker /*recipient*/, false, X, priceLimit, "", extensionData)
   msg.sender of this call = address(router).

6. Pool calls _beforeSwap(sender=router, recipient=attacker, ...).

7. SwapAllowlistExtension.beforeSwap receives sender=router.
   Checks: allowedSwapper[pool][router] → true  ✓
   Hook passes.

8. Swap executes. attacker receives output tokens.
   The allowlist was never consulted for attacker's address.

Expected result without the bug
────────────────────────────────
Step 7 should check the actual initiating user (attacker), find it not allowlisted,
and revert with NotAllowedToSwap.

Foundry test skeleton
─────────────────────
function test_routerBypassesAllowlist() public {
    // allowlist only the router, not the attacker
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // attacker (not allowlisted) calls through router
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool), recipient: attacker,
        zeroForOne: false, amountIn: 1000, ...
    }));
    // swap succeeds — allowlist bypassed
}
```