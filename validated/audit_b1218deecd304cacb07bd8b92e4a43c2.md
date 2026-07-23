Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` (the immediate caller) as the `sender` argument to `_beforeSwap`, which forwards it verbatim to `SwapAllowlistExtension.beforeSwap`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps for authorized users inadvertently grants unrestricted swap access to every address on the network.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap() — the router, not the user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` gates on that value keyed by the calling pool (`msg.sender`):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

In `MetricOmmSimpleRouter.exactInputSingle`, the real user's address (`msg.sender`) is stored only in transient storage for the payment callback and is never forwarded to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
```

The pool therefore sees `msg.sender = router`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same pattern applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181) — all four entry points call `pool.swap()` with the router as `msg.sender` and never pass the originating user to the pool or any extension.

Existing guards are insufficient: `SwapAllowlistExtension` has no mechanism to distinguish a router-mediated call from a direct call, and there is no `tx.origin` or forwarded-origin check anywhere in the extension or pool.

## Impact Explanation
This is a complete admin-boundary break. A pool admin configuring a restricted pool faces an impossible choice: allowlisting individual users blocks them from using the standard router (the extension sees `sender = router`, not the user), while allowlisting the router grants unrestricted swap access to every address. The realistic operational path — allowlisting the router — renders the allowlist entirely ineffective. Any non-allowlisted address can execute swaps in a pool explicitly configured to be restricted, directly impacting fund flows and violating the admin-configured access-control boundary.

## Likelihood Explanation
The trigger requires only that the pool admin allowlists the router, which is the natural operational step for any restricted pool intended to be usable via standard periphery. No privileged attacker capability is needed; any EOA or contract can call the public router functions. The bypass is unconditional and repeatable once the router is allowlisted.

## Recommendation
The originating user must be propagated through the call chain so the extension can gate on it:

1. **Router**: pass the real user's address to the pool via `extensionData` or a dedicated `originSender` field in `pool.swap()` parameters.
2. **SwapAllowlistExtension**: when the immediate `sender` is a known router, extract and verify the forwarded origin address rather than the raw `sender` argument.

A short-term documentation mitigation (do not allowlist the router; allowlisted users must call `pool.swap()` directly) breaks the intended UX and is not a code-level fix.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  pool admin: setAllowedToSwap(pool, alice, true)    // alice is the only intended trader

Attack:
  eve (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: eve})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=eve, ...)        [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes; eve receives output tokens

Result:
  eve bypasses the allowlist entirely.
  alice's exclusive access is nullified.
  Any address can drain the restricted pool via the router.
```