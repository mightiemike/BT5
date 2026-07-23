Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If a pool admin allowlists the router to enable router-mediated access, every unprivileged address can bypass the allowlist unconditionally by calling any router entry point.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that value, keyed by `msg.sender` (the pool):

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

The pool therefore receives `msg.sender = router`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual user's identity is present in the transaction context but is absent from the guard's verification. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, including the recursive callback path in `_exactOutputIterateCallback` where intermediate hops call `pool.swap()` with `msg.sender = address(this)` (the router).

## Impact Explanation
A pool admin configuring a restricted pool faces an impossible choice: allowlisting individual users blocks router-mediated swaps for those users (the extension sees `sender = router`, not in the allowlist), while allowlisting the router opens the pool to every address on the network. The realistic operational path — allowlisting the router so that authorized users can use the standard periphery — results in a complete bypass of the admin-configured access-control boundary. Any non-allowlisted EOA or contract can execute swaps in a pool explicitly configured to be restricted, constituting an admin-boundary break with direct fund-flow consequences.

## Likelihood Explanation
The trigger requires only that the pool admin allowlists the router address, which is the natural and expected step when deploying a restricted pool intended to be usable via the standard periphery. No privileged attacker capability is needed; any EOA or contract can call the public router functions. The bypass is unconditional and repeatable once the router is allowlisted.

## Recommendation
The actual initiating user must be forwarded through the call chain so the extension can gate on it:
1. **Router**: pass the real user's address to the pool via `extensionData` or a dedicated `originSender` parameter in `pool.swap()`.
2. **SwapAllowlistExtension**: when the immediate `sender` is a known router, extract and gate on the forwarded origin address rather than the raw `sender` argument.

As a short-term mitigation, document that the router must not be allowlisted and that allowlisted users must call `pool.swap()` directly — but this breaks the intended UX and is not a code-level fix.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)
    → intended to let allowlisted users reach the pool via the router
  pool admin calls setAllowedToSwap(pool, alice, true)
    → alice is the only intended authorized trader

Attack:
  eve (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: eve})

Execution trace:
  router.exactInputSingle()
    → _setNextCallbackContext(..., msg.sender=eve, ...)   [stored in transient storage only]
    → pool.swap(recipient=eve, ...)                       [msg.sender = router]
      → _beforeSwap(sender=router, recipient=eve, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓ (no revert)
      → swap executes, eve receives output tokens

Result:
  eve bypasses the allowlist entirely.
  alice's exclusive access is nullified.
```