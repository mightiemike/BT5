Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` populates with its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool call, so `sender` equals the router address rather than the end user. Any pool admin who allowlists the router to enable router-based swaps for legitimate users simultaneously opens the allowlist to every user who routes through it.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← always the immediate caller, not the economic actor
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged as `sender` to the extension hook. `SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(params.recipient, ...)`, the router is `msg.sender` of that call. The extension receives `sender = router`, checks `allowedSwapper[pool][router]`, and if the router is allowlisted, the check passes regardless of who the actual end user is. The `recipient` argument — which identifies the economic beneficiary — is the second parameter of `beforeSwap` but is explicitly unnamed and ignored in the extension. `DepositAllowlistExtension` avoids this flaw by checking `owner` (the position beneficiary passed explicitly), not `sender`.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known counterparty set loses that restriction entirely for any user routing through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps and receive output tokens at oracle-derived prices the pool admin intended to reserve for specific parties. This constitutes direct loss of LP principal and a broken core pool invariant (curation/access policy). The corrupted value is the extension's access-control decision: `allowedSwapper[pool][router] = true` is evaluated instead of `allowedSwapper[pool][actualUser]`, causing the hook to return `IMetricOmmExtensions.beforeSwap.selector` when it should revert.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery path. Any pool admin who enables router-based swaps for allowlisted users must call `setAllowedToSwap(pool, router, true)`, which simultaneously opens the bypass for all other users. No privileged access, malicious setup, or non-standard tokens are required — only a standard router call from any EOA or contract.

## Recommendation
Replace the `sender` check with a `recipient` check in `beforeSwap`. The `recipient` is the address that receives swap output regardless of routing path, making it the correct actor to gate:

```solidity
// fixed
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, require the router to encode the originating user in `extensionData` and decode it in the extension, but the `recipient` fix is simpler and requires no router changes.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` and `setAllowedToSwap(pool, router, true)` (required for any router-based swap to succeed).
3. `userB` (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: userB, ...})`.
4. The router calls `pool.swap(recipient=userB, ...)` — `msg.sender` of the pool call is the router.
5. `_beforeSwap` is called with `sender = router`; `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → returns selector without reverting.
6. `userB` receives output tokens from the curated pool despite never being individually allowlisted.

Foundry test sketch:
```solidity
vm.prank(userB);
router.exactInputSingle(ExactInputSingleParams({
  pool: curatedPool, recipient: userB, tokenIn: token0,
  amountIn: 1e18, amountOutMinimum: 0, zeroForOne: true,
  priceLimitX64: 0, deadline: block.timestamp, extensionData: ""
}));
// userB receives token1 — allowlist bypassed
```