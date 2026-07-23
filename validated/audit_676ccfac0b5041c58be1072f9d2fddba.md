Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Allowlist via the Standard Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` at the pool level — the router address when a swap is routed through `MetricOmmSimpleRouter`. Any pool admin who allowlists the router (required for router-mediated swaps to function) simultaneously opens the gate to every user on the network, completely defeating the allowlist's curated-access invariant.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder` (L160-176). `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check asks "is the router allowlisted?" — not "is the end user allowlisted?"

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no forwarding of the original caller:

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

The actual caller (`msg.sender` of `exactInputSingle`) is never visible to the extension. The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` as `msg.sender == router`.

The pool admin faces an impossible choice:
- **Do not allowlist the router**: allowlisted users cannot use the standard router; must call the pool directly.
- **Allowlist the router**: every user on the network can bypass the allowlist via the router.

Existing guards are insufficient: the only check in `beforeSwap` is `allowedSwapper[pool][sender]`, and there is no mechanism to recover the original EOA from the router call.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified users, whitelisted market makers). Once the router is allowlisted — the only way to let any allowlisted user trade through the standard periphery — the gate is open to the entire public. Non-allowlisted users can execute swaps at oracle-anchored prices, draining LP value or extracting arbitrage that the pool's curation policy was designed to prevent. This constitutes a broken core pool invariant (curated access) and a direct loss of LP principal.

## Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` and expects users to interact through `MetricOmmSimpleRouter` is immediately vulnerable. The router is the standard periphery entry point. A pool admin following the normal integration path will allowlist the router, unknowingly opening the bypass. The attacker requires no special role, no privileged setup, and no non-standard token — a single call to `exactInputSingle` suffices.

## Recommendation
The extension must gate the economic actor, not the immediate pool caller. Two complementary fixes:

1. **Pass the original caller through the router.** The router should forward `msg.sender` to the pool (e.g., via `callbackData` or a dedicated originator field), and the pool should pass it as a separate `originator` argument to extensions.
2. **Check `originator` in the allowlist extension.** `SwapAllowlistExtension.beforeSwap` should check the originator address when the immediate sender is a known periphery contract, or always check the originator when it is provided.

A short-term mitigation: document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call the pool directly. This is a severe UX restriction that underscores the need for the structural fix.

## Proof of Concept
```
Setup:
  - Pool P deployed with SwapAllowlistExtension E
  - Pool admin calls E.setAllowedToSwap(P, alice, true)   // alice is KYC'd
  - Pool admin calls E.setAllowedToSwap(P, router, true)  // needed for alice to use router
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, ...) — msg.sender at pool = router
  3. Pool calls _beforeSwap(router, ...)
  4. Extension checks allowedSwapper[P][router] → true
  5. Swap executes for bob with no revert

Result:
  bob trades on a curated pool that was supposed to block him,
  bypassing the allowlist entirely.
```