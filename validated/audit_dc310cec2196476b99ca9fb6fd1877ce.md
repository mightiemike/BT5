Audit Report

## Title
SwapAllowlistExtension Bypassed via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as the `sender` argument to `_beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. `SwapAllowlistExtension.beforeSwap` checks this `sender` against the per-pool allowlist, so any non-allowlisted user can bypass the gate by calling through the router if the router itself is allowlisted.

## Finding Description
**Root cause — `sender` binding in `MetricOmmPool.swap`:**

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller, not the economic actor
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) is used, it calls `IMetricOmmPoolActions(params.pool).swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

So the pool sees `msg.sender = address(router)`. `_beforeSwap` forwards this as `sender` to `SwapAllowlistExtension.beforeSwap`:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router address. If the pool admin allowlists the router (the natural setup for a public router), every user — including those explicitly excluded from the allowlist — can swap freely by routing through it.

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension`, allowlists specific addresses, and also allowlists `MetricOmmSimpleRouter` so legitimate users can use the router.
2. Attacker (not on the allowlist) calls `router.exactInputSingle(...)` targeting the restricted pool.
3. Pool receives `msg.sender = router`, which is allowlisted → `beforeSwap` passes.
4. Attacker executes a swap that the allowlist was designed to prevent.

**Existing guards are insufficient:** The `nonReentrant` guard and `whenNotPaused` check in `swap` do not inspect the economic actor. There is no mechanism in the pool or router to forward the original `msg.sender` to the extension.

## Impact Explanation
The `SwapAllowlistExtension` is a core access-control feature. Its bypass allows any unprivileged user to trade in pools that pool admins intended to restrict, directly breaking the allowlist invariant. Depending on the pool's purpose (e.g., KYC-gated, institutional-only, or whitelist-only liquidity), this can result in unauthorized fund flows and loss of LP principal through unwanted price impact from disallowed traders. This meets the "broken core pool functionality causing loss of funds" threshold.

## Likelihood Explanation
The bypass requires only that the router be allowlisted on the target pool — a routine and expected configuration for any pool that wants to support standard router-based trading. The attacker needs no special privileges, no capital beyond the swap amount, and can repeat the attack on every block. The condition is highly likely to be met in production deployments.

## Recommendation
Pass the economic actor's address rather than the immediate caller. One approach: add an optional `sender` override parameter to `swap` that the router populates with `msg.sender` (the end user), and have the pool verify the override is only accepted from factory-registered routers. Alternatively, `SwapAllowlistExtension.beforeSwap` should check both `sender` (the direct caller) and a router-forwarded originator field if the caller is a known router, or the pool should expose a `swapWithSender` entry point that trusted routers call with the real user address.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin allowlists router (address(router)) and alice, but NOT bob
extension.setAllowedToSwap(address(pool), address(router), true);
extension.setAllowedToSwap(address(pool), alice, true);
// bob is NOT allowlisted

// 3. Bob calls directly → reverts correctly
vm.prank(bob);
pool.swap(...); // reverts NotAllowedToSwap ✓

// 4. Bob calls via router → succeeds (bypass)
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: ...,
    ...
})); // succeeds — allowlist bypassed ✗
```