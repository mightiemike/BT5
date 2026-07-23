Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Making the Allowlist Bypassable or Broken for Router-Mediated Swaps - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that caller is the router contract, not the end user. The allowlist therefore checks the wrong actor, producing either a complete bypass (if the router is allowlisted) or broken core swap functionality (if only individual users are allowlisted).

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap(), not the originating user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool's `msg.sender` is the router contract. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation

Two mutually exclusive fund-impacting failure modes exist:

**Mode A — Allowlist bypass:** If the pool admin allowlists the router address to permit router-mediated swaps, every user — including those explicitly excluded — can bypass per-user restrictions by routing through `MetricOmmSimpleRouter`. The curated pool's access control is completely defeated.

**Mode B — Broken core functionality:** If the pool admin allowlists only individual user addresses (the intended design), those allowlisted users cannot use the router at all. Their calls revert with `NotAllowedToSwap` because `allowedSwapper[pool][router]` is false. The primary public swap entrypoint is unusable for the pool's legitimate participants.

No allowlist configuration correctly enforces per-user restrictions while also permitting router-mediated swaps.

## Likelihood Explanation

Any pool deploying `SwapAllowlistExtension` to restrict swaps to a curated set of addresses immediately encounters this issue the first time an allowlisted user attempts to swap through the router. The router is the primary public entrypoint. No privileged action beyond standard pool setup and a normal router call is required to trigger either failure mode.

## Recommendation

The pool's `swap` function should accept an explicit `sender` parameter (analogous to how `addLiquidity` accepts an explicit `owner`) so the router can forward `msg.sender` (the real user) and the pool encodes it as the authoritative sender for extension checks. Alternatively, the router can supply the real user address in `extensionData` and the extension can decode it — but this is forgeable by direct callers and requires careful trust modeling.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowlisted swapper.
3. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router address.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `false` → reverts `NotAllowedToSwap`.
7. Alice's swap fails despite being explicitly allowlisted. (**Mode B**)

**Bypass variant:**
1. Pool admin additionally calls `setAllowedToSwap(pool, router, true)` to fix Alice's problem.
2. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
3. Extension checks `allowedSwapper[pool][router]` → `true` → swap succeeds.
4. Bob bypasses the allowlist entirely. (**Mode A**)