Audit Report

## Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps using `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the pool's `msg.sender` — the router contract — when users route through `MetricOmmSimpleRouter`. A pool admin who allowlists the router (the only way to enable router-mediated swaps) inadvertently grants every on-chain address the ability to bypass the allowlist, because the extension always sees the router address, never the real user's address.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 230–231, passing the pool's `msg.sender` as `sender`:

```solidity
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` (lines 149–177) encodes this `sender` verbatim into the `beforeSwap` call dispatched to every extension. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `IMetricOmmPoolActions(params.pool).swap(...)`, the pool's `msg.sender` is the router contract, not the end user.

`SwapAllowlistExtension.beforeSwap` (line 37) then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check becomes `allowedSwapper[pool][router]`. To make router-mediated swaps work at all, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once that is done, every caller of the public router passes the check — the extension never inspects the actual user's address. The unit test confirms the first positional argument is the identity checked, but the test only exercises direct pool calls (`vm.prank(address(pool))`), not router-mediated paths.

## Impact Explanation
Any pool configured with `SwapAllowlistExtension` for access control (KYC, institutional, permissioned liquidity) is fully open to any EOA or contract that calls `MetricOmmSimpleRouter`. The allowlist invariant — "only approved addresses may trade" — is structurally broken for all router-mediated swaps. Unauthorized users can drain one-sided liquidity, extract arbitrage, or interact with pools they are explicitly excluded from, causing direct loss of LP principal and fee revenue. This meets the "broken core pool functionality causing loss of funds" criterion.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public entry point for swaps. Any pool using `SwapAllowlistExtension` that needs to support router-mediated swaps must allowlist the router, immediately triggering the vulnerability. No special privilege is required — any EOA calls the public router. The condition is self-imposed by the admin trying to make the extension work correctly with the router.

## Recommendation
Pass the original user's address through the swap call chain rather than relying on `msg.sender` at the pool boundary. Two standard approaches:

1. **Router forwards caller identity via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension reads it from there, verifying `msg.sender` (the pool) is the actual caller so the data cannot be spoofed by a direct caller.
2. **Pool `swap()` accepts an explicit `sender` override**: The router populates it with its own `msg.sender`; the pool validates `msg.sender == router` before trusting the override.

Either approach ensures the allowlist checks the economically relevant actor — the wallet initiating the trade — rather than the intermediary router.

## Proof of Concept
```
1. Deploy a pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — required to allow any router-mediated swap.
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle(...) targeting the pool.
5. Router calls IMetricOmmPoolActions(pool).swap(attacker_as_recipient, ...).
6. Pool calls _beforeSwap(router_address, ...) — sender = router.
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
8. Swap executes. Attacker receives output tokens.
9. attacker's address was never checked; the allowlist is bypassed.
```

Foundry test plan: deploy pool + extension, allowlist only the router, prank as an unlisted EOA calling `exactInputSingle`, assert the swap succeeds (demonstrating bypass) and that a direct `pool.swap` from the same EOA reverts with `NotAllowedToSwap`.