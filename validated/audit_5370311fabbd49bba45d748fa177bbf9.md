All four code references in the claim have been verified against the actual repository. The vulnerability is confirmed:

1. `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` — confirmed at lines 230–240.
2. `ExtensionCalling._beforeSwap` encodes `sender` verbatim — confirmed at lines 149–177.
3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct caller of `pool.swap` — confirmed at lines 31–41.
4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` with the router as `msg.sender` — confirmed at lines 71–80.

The dilemma is real and irresolvable in the current code: allowlisting the router opens the gate to all users; not allowlisting it breaks router-mediated swaps for allowlisted users. The pool admin allowlisting the router is not a malicious setup — it is the only rational configuration for a pool that wants allowlisted users to benefit from router features (slippage protection, deadline, multi-hop). The exploit is then triggered by any unprivileged user calling `exactInputSingle`.

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, enabling full allowlist bypass for any user routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` gates pool swaps by checking the `sender` argument forwarded by the pool. Because `MetricOmmPool.swap` always passes `msg.sender` as `sender`, and `MetricOmmSimpleRouter` is `msg.sender` when routing a swap, the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router to enable router-mediated swaps for their allowlisted users simultaneously opens the pool to every user on the router, defeating the allowlist entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` encodes that value verbatim into the hook call:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` of `pool.swap`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin faces an irresolvable dilemma: if the router is not allowlisted, allowlisted users cannot use the router at all; if the router is allowlisted (the only way to enable router-mediated swaps), every user on the router bypasses the allowlist. No existing guard in the extension or the pool checks the originating user address when the caller is a router.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps at oracle-derived prices, draining LP value or violating the pool's intended access policy. This is a direct loss of LP principal and a broken core pool invariant (the allowlist guard). The corrupted value is the identity checked by the guard: the router address is substituted for the actual user address, causing the allowlist to pass where it must block.

## Likelihood Explanation
The router is the primary user-facing swap entry point in the periphery. Any pool admin who wants allowlisted users to benefit from multi-hop routing, exact-output swaps, or deadline/slippage protection must allowlist the router. The moment they do, the bypass is live for all users. The trigger requires no privileged access, no special token behavior, and no unusual timing — any public user can call `exactInputSingle` on the router against the pool.

## Recommendation
The extension must gate the actual end-user, not the direct caller of `pool.swap`. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the originating user address into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. This requires a trusted router registry or a signed payload.

2. **Sender-only policy**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at pool creation (e.g., factory-level validation that rejects pools combining this extension with a public router allowlist entry). Pool admins must allowlist individual user addresses and those users must call `pool.swap` directly.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended gated user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // necessary for alice to use the router
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient=bob, ...) with msg.sender = router.
6. _beforeSwap forwards sender = router to SwapAllowlistExtension.beforeSwap.
7. Extension evaluates: allowedSwapper[pool][router] == true  →  passes.
8. bob's swap executes at oracle price, bypassing the allowlist entirely.
```