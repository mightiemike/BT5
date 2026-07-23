Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the original swapper, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` — the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, that `msg.sender` is the router contract, not the original EOA. Any pool admin who allowlists the router (the only way to enable router-based swaps for legitimate users) simultaneously opens the gate to every non-allowlisted address, completely defeating the allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool.

`MetricOmmPool.swap` always passes its own `msg.sender` as that first argument:
```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```
`ExtensionCalling._beforeSwap` then encodes that value verbatim as the `sender` argument in the extension call.

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the router contract. The original EOA's identity is stored only in transient storage for the payment callback (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`) and is never forwarded to the pool or extension as the swapper identity.

Therefore the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][original_user]`. If the admin allowlists the router — the only way to make router-based swaps work for legitimate users — every address, including those the admin explicitly never allowlisted, passes the check.

Contrast with `DepositAllowlistExtension.beforeAddLiquidity`, which checks `owner` — an explicit argument that the liquidity adder passes through unchanged as the actual position owner — correctly identifying the economic actor regardless of the intermediary.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router to enable router access for permitted users inadvertently opens the pool to all users. Any non-allowlisted address can call `router.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the extension will pass because it sees `sender = router`. The allowlist is completely ineffective for router-mediated swaps. Non-permitted actors can drain LP value from a pool designed to be restricted, constituting a direct loss of user principal and broken core pool functionality (the allowlist guard).

## Likelihood Explanation
The scenario requires the pool admin to allowlist the router. This is the natural, expected action for any operator who wants their allowlisted users to use the standard periphery. The router is a first-party, factory-registered contract; allowlisting it is not a misconfiguration in isolation — it is the only way to make the router work on an allowlisted pool. The bypass is therefore reachable on any production curated pool that supports router access, with no special attacker capability required beyond calling a public router function.

## Recommendation
The extension must gate the original user, not the immediate pool caller. The cleanest fix is for `MetricOmmSimpleRouter` to append `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool, and for `SwapAllowlistExtension` to decode and verify that address when `sender` is a known router address. Alternatively, the factory could maintain a registry of known routers, and the extension could fall back to checking a user-supplied identity in `extensionData` when `sender` is a registered router.

## Proof of Concept
```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls swapExtension.setAllowedToSwap(pool, alice, true)
  admin calls swapExtension.setAllowedToSwap(pool, router, true)
    // ← necessary so alice can use the router

Attack (executed by bob, who is NOT allowlisted):
  bob calls router.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

Execution trace:
  router.exactInputSingle (msg.sender = bob)
    → pool.swap(recipient=bob, ...) [msg.sender in pool = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ← PASSES
      → swap executes, bob receives tokens

Result:
  bob swaps successfully on a pool restricted to alice only.
  The allowlist is completely bypassed.
```