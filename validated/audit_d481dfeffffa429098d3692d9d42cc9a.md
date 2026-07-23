Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is used, `msg.sender` at the pool is the router contract, not the end user. Allowlisting the router — the only way to permit router-mediated swaps — opens the gate to every address on-chain, completely defeating the allowlist.

## Finding Description
The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value directly to the extension. Inside `SwapAllowlistExtension.beforeSwap`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check becomes `allowedSwapper[pool][router]`, which is true for every user routing through the shared router contract.

`MetricOmmSimpleRouter.exactInputSingle` never forwards the original caller's identity — it calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

This creates two broken states with no valid configuration:

| Pool admin action | Effect |
|---|---|
| Allowlists the router | Every address can swap; allowlist fully defeated |
| Does not allowlist the router | Even allowlisted users cannot swap through the router |

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the explicit position owner argument) rather than `sender` (the intermediary), which is the correct pattern.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Once the router is allowlisted — the only way to let legitimate users use the standard periphery — the gate is open to every address. Unauthorized swappers can extract value from LP positions at oracle-derived prices the pool admin did not intend to offer them, or drain one side of the pool if the oracle price diverges from market, causing direct LP principal loss. This constitutes broken core pool functionality with direct loss of user principal.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard swap entry point. Any pool enabling `SwapAllowlistExtension` that wants users to swap through the router must allowlist the router, automatically triggering the bypass. No special privilege, flash loan, or unusual token behavior is required — a plain `exactInputSingle` call suffices. The allowlist state is publicly readable on-chain via `allowedSwapper`.

## Recommendation
The extension must gate the economic actor, not the intermediary:

1. **Pass the original user through the router.** Add a `payer`/`originator` field to `extensionData` that the router populates with `msg.sender`, and have the extension decode and check that address. This requires a coordinated router + extension change.
2. **Check `recipient` instead of (or in addition to) `sender`.** The extension already receives `recipient` as its second argument but ignores it. For swap allowlists the economically relevant identity is often the recipient of output tokens.
3. **Document incompatibility.** If neither fix is applied, NatSpec must warn that allowlisting any shared intermediary defeats the gate.

The `DepositAllowlistExtension` avoids this problem by checking `owner` rather than `sender`, which is the correct pattern to follow.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  - Bob (not allowlisted) calls:
      router.exactInputSingle({
          pool:      pool,
          recipient: bob,
          zeroForOne: true,
          amountIn:  X,
          ...
      })
  - Router calls pool.swap(bob, true, X, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → passes
  - Bob's swap executes; allowlist is bypassed.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; Bob receives output tokens.
```