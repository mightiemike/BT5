Audit Report

## Title
`SwapAllowlistExtension` receives router address as `sender` instead of the originating user, enabling full allowlist bypass for any caller — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. If the pool admin allowlists the router address (a natural step to enable router-mediated swaps for curated pools), every unprivileged address can bypass the per-user swap gate by routing through the router.

## Finding Description

**Root cause — pool passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` (line 231):
```solidity
_beforeSwap(
    msg.sender,   // <-- always the immediate caller, not the originating user
    recipient,
    ...
);
```

**Extension checks that value against its allowlist:**

`SwapAllowlistExtension.beforeSwap` (line 37):
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

**Router calls the pool directly, substituting itself as `sender`:**

`MetricOmmSimpleRouter.exactInputSingle` (line 72–80):
```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
```
The router never forwards the originating `msg.sender` to the pool. From the pool's perspective, `msg.sender == router`, so `sender == router` reaches the extension.

**Exploit flow:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists specific user addresses (e.g., KYC'd wallets).
2. Admin also calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for those users (a natural operational step).
3. Any unprivileged address calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. The router calls `pool.swap(...)` → pool calls `_beforeSwap(router, ...)` → extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. The non-allowlisted user's swap executes on a pool that was supposed to be restricted.

**Existing guards are insufficient:** The extension has no mechanism to recover the originating user from the router call. The `sender` argument is the only identity signal, and it is structurally wrong for all router-mediated paths (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, and the recursive `_exactOutputIterateCallback` hop at line 220–228).

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to enforce per-user access control (e.g., KYC, whitelist, institutional-only) is fully bypassed for any caller once the router is allowlisted. Unauthorized users can execute swaps, draining LP-provided liquidity at oracle prices, which constitutes broken core pool functionality and potential direct loss of LP assets. This matches the "allowlist bypass" allowed impact and the "broken core pool functionality causing loss of funds" gate.

## Likelihood Explanation
The precondition — router being allowlisted — is a natural operational step for any pool admin who wants to support the official periphery router alongside a curated allowlist. The admin cannot grant router access to specific users only; the only available granularity is the router address itself. Any unprivileged address can then exploit this by calling the router, with no special capability required. The attack is repeatable on every swap.

## Recommendation
Pass the originating user identity through the call chain. One approach: have the router encode the originating `msg.sender` in `extensionData` and have the extension decode and verify it. A cleaner approach: add a `swapper` field to the pool's `swap` signature that the router populates with `msg.sender`, and have the pool forward that value (not its own `msg.sender`) as `sender` to extensions. Alternatively, `SwapAllowlistExtension` should explicitly reject the known router address as a `sender` and require direct calls only, or the pool should expose a trusted-forwarder pattern so the router can attest the originating user.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Admin allowlists alice and the router
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// 3. Attacker (not allowlisted) calls router
vm.prank(attacker); // attacker != alice, not in allowlist
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds — allowlist bypassed
```