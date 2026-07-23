Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` delivered to the extension is the router address — not the actual end-user. If the pool admin allowlists the router to enable router-based swaps for intended participants, every unprivileged user can bypass the per-user gate by routing through the same public router.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, forwarding its own `msg.sender` as the `sender` argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap` encodes that value verbatim and dispatches it to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on `(msg.sender=pool, sender=caller-of-pool)`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router, so `sender` delivered to the extension is the router address:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The router stores the actual end-user (`msg.sender`) in transient storage via `_setNextCallbackContext` for the payment callback only — it is never passed to the pool as `sender`. The extension has no way to recover the real user.

This produces two mutually exclusive failure modes:

1. **Router not allowlisted** — every allowlisted user who calls through the router is rejected (`NotAllowedToSwap`), making the standard swap path unusable for the pool's intended participants.
2. **Router allowlisted** — every user, allowlisted or not, can swap freely through the router, completely defeating the per-user gate.

Existing guards are insufficient: `BaseMetricExtension` only validates that `msg.sender` is a registered pool; it does not recover or validate the original end-user identity. The `extensionData` field is passed through but `SwapAllowlistExtension.beforeSwap` ignores it entirely (the `bytes calldata` parameter is unnamed and unused).

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties, whitelisted protocols, or institutional LPs) cannot enforce that restriction when the public `MetricOmmSimpleRouter` is used. Once the pool admin allowlists the router to unblock intended participants, any unprivileged user can route through the router and trade against the pool's liquidity without being on the allowlist. This is an admin-boundary break: an access control the pool admin explicitly configured is bypassed by an unprivileged path (the public router), allowing unauthorized swaps against pool liquidity.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any user who discovers the allowlist restriction on direct `pool.swap()` calls can trivially route through the router instead. No special privileges, flash loans, or complex setup are required — a single `exactInputSingle` call suffices. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Recommendation

Pass the original end-user identity through the call chain so the extension can gate on it:

1. **Preferred — encode the real payer in `extensionData`**: The router already knows `msg.sender` (the real user). It should encode it into `extensionData` before forwarding to the pool, and `SwapAllowlistExtension` should decode and check that address when `extensionData` is present and the caller is a known router.

2. **Alternative — add a `payer` field to the swap interface**: Extend `IMetricOmmPoolActions.swap` with an explicit `payer` argument (the economic actor), distinct from `recipient`. The pool passes `payer` as `sender` to extensions, and the router sets it to `msg.sender`.

Until fixed, pools that require per-user swap restrictions must not rely on `SwapAllowlistExtension` when the router is accessible.

## Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E configured.
2. Admin calls E.setAllowedToSwap(P, alice, true)  — only alice is allowed.
3. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: P, ...})
4. Router calls P.swap(recipient, ...) — pool sees msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. Extension checks allowedSwapper[P][router] → false → reverts NotAllowedToSwap.
   (Failure mode 1: alice also blocked via router)

7. Admin, wanting router-based swaps to work, calls:
       E.setAllowedToSwap(P, router, true)
8. Bob calls router.exactInputSingle({pool: P, ...}) again.
9. Extension checks allowedSwapper[P][router] → true → swap proceeds.
   → Bob (not allowlisted) successfully swaps. (Failure mode 2: allowlist bypassed)
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist only `alice`, allowlist the router, then call `router.exactInputSingle` as `bob` and assert the swap succeeds despite `bob` not being on the allowlist.