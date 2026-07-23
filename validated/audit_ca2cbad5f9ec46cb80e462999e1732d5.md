Audit Report

## Title
SwapAllowlistExtension checks router address instead of actual user — allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the pool's `msg.sender` — the router contract — not the originating user. When a user swaps through `MetricOmmSimpleRouter`, the extension sees the router address as the swapper identity, not the actual trader. This means any user can bypass a per-user allowlist by routing through the router if the router address is allowlisted, and conversely, legitimately allowlisted users are blocked from using the router entirely.

## Finding Description

**Root cause — wrong identity in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` (line 37):
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
`msg.sender` here is the pool (the extension's caller). `sender` is the first argument, which the pool sets to its own `msg.sender` at `MetricOmmPool.swap` line 231:
```solidity
_beforeSwap(
    msg.sender,   // <-- pool's msg.sender, i.e. the router
    recipient,
    ...
);
```

**Router call path:**

`MetricOmmSimpleRouter.exactInputSingle` (line 72–80) calls:
```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```
The router is `msg.sender` of `pool.swap()`. Therefore `sender` delivered to the extension is the **router address**, not the originating user.

**Exploit flow:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific user addresses (e.g., KYC'd traders).
2. Pool admin also allowlists the router address so that router-mediated swaps are permitted (a natural operational step).
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the allowlisted pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`.
5. The extension checks `allowedSwapper[pool][router]` — which is `true` — and the swap proceeds.
6. The non-allowlisted user successfully swaps on a pool that was supposed to restrict them.

**Existing guards are insufficient:** The extension has no mechanism to recover the original `tx.origin` or a forwarded user identity. The `sender` argument is structurally bound to the pool's `msg.sender`, which is always the router for router-mediated swaps. There is no on-chain path for the extension to distinguish the actual trader.

**Secondary broken-functionality impact:** If the pool admin does *not* allowlist the router, then legitimately allowlisted users are blocked from using the router entirely, making the primary supported swap entry point unusable for curated pools.

## Impact Explanation
Direct allowlist bypass on curated pools: any unprivileged trader can execute swaps on a pool that the pool admin intended to restrict to a specific set of addresses. This constitutes a broken core pool functionality (the allowlist guard fails open for all router-mediated swaps when the router is allowlisted) and an admin-boundary break (the pool admin's curation policy is bypassed by an unprivileged path). The exact corrupted value is `allowedSwapper[pool][sender]` — the wrong key is evaluated, causing the guard to authorize the router contract rather than the actual trader.

## Likelihood Explanation
The router is the canonical, documented user-facing entry point for swaps. Any pool admin who enables router-mediated swaps on an allowlisted pool must allowlist the router, at which point the bypass is immediately available to every unprivileged user. The attacker needs no special capability: a standard `exactInputSingle` call suffices. The condition is repeatable across every block.

## Recommendation
The extension must gate on the economically relevant actor, not the pool's immediate caller. Options:
1. Have the router forward the originating user address in `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when present.
2. Require direct pool interaction for allowlisted pools (document and enforce that the router is incompatible with `SwapAllowlistExtension`).
3. Redesign the extension interface so the pool passes both `msg.sender` (the direct caller) and a separately authenticated `originator` field that the router populates via a signed or transient-storage mechanism.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin allowlists router: swapExtension.setAllowedToSwap(pool, address(router), true)
// 3. Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] == false
// 4. Attacker calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: allowlistedPool,
    tokenIn: token0,
    recipient: attacker,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// 5. Pool calls _beforeSwap(msg.sender=router, ...)
// 6. Extension checks allowedSwapper[pool][router] == true → passes
// 7. Attacker swaps successfully despite not being on the allowlist
```