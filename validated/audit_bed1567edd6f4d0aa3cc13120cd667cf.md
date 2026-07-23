Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating EOA, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which `MetricOmmPool.swap` binds to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating EOA. Any pool admin who allowlists the router address inadvertently opens the pool to all users, completely defeating the per-pool swap allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // = router address, not originating EOA
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

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) revert ...
// resolves to: allowedSwapper[pool][router]
```

`MetricOmmSimpleRouter.exactInputSingle` stores the originating EOA only in transient callback context for payment settlement (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`) and never forwards it to the pool. The pool's `swap` interface has no `originator` parameter — the pool always uses `msg.sender`. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation
The swap allowlist's core invariant — "only explicitly allowlisted addresses may swap" — is broken for all router-mediated swaps. When a pool admin calls `setAllowedToSwap(pool, address(router), true)` to enable router-mediated swaps for their allowlisted users, every unprivileged EOA gains unrestricted swap access to that pool. This constitutes broken core pool functionality: the access-control extension produces no effective gate for router-mediated swaps.

## Likelihood Explanation
A pool admin who wants to allow their allowlisted users to trade via the router has no other option than to allowlist the router address itself. The extension provides no mechanism to propagate the originating EOA through an intermediary. The misconfiguration is therefore a predictable consequence of normal, non-malicious admin usage. The router is a public, permissionless contract callable by any EOA.

## Recommendation
The extension or pool must propagate the originating caller identity through the router. Two options:

1. **Pass originator in `extensionData`:** The router encodes `msg.sender` into `extensionData`; `SwapAllowlistExtension` decodes and checks it. This requires a convention between router and extension.
2. **Add an explicit `originator` field to the swap interface:** The pool's `swap` signature accepts an `originator` address (defaulting to `msg.sender` for direct calls); the router passes `msg.sender` there. The extension checks `originator` instead of `sender`.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
// 2. Admin allowlists only the router:
//    setAllowedToSwap(pool, address(router), true)
// 3. Non-allowlisted EOA calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    ...
}));
// 4. MetricOmmSimpleRouter calls pool.swap() → msg.sender at pool = router
// 5. _beforeSwap(router, ...) → allowedSwapper[pool][router] == true → passes
// 6. Swap executes for the non-allowlisted EOA — allowlist bypassed
```