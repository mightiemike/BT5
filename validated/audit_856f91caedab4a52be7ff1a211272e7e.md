Audit Report

## Title
Swap Allowlist Checks Router Address Instead of Original User — Complete Allowlist Bypass via Router (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the `pool.swap` call — the router contract, not the originating user. When a pool admin allowlists the router to permit router-mediated swaps, every unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`, because the extension sees only the router address and finds it allowlisted.

## Finding Description

**Call path:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. The router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` inside the pool is the **router**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231, passing the **router address** as `sender`.
4. `ExtensionCalling._beforeSwap` encodes `sender` (router) and dispatches to the configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.
5. `SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the **router address** (wrong — should be the originating user). The check `allowedSwapper[pool][router]` passes whenever the pool admin has allowlisted the router, which is required for any router-mediated swap to function at all.

**Root cause:** The pool passes `msg.sender` (the immediate caller) as `sender` to extensions. The router does not forward the originating user's address; it is the direct caller of `pool.swap`. The extension has no mechanism to recover the true user identity.

**Existing guards are insufficient:** `_requireExpectedCallbackCaller` in the router only validates that the callback comes from the expected pool; it does not inject user identity into the swap call. There is no on-chain path by which the extension can distinguish two different users routing through the same router instance.

**Exact wrong value:** `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][originalUser]`.

## Impact Explanation
Any unprivileged user can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter`. The pool admin's intent — to restrict trading to a specific set of addresses — is completely defeated. This constitutes a broken core access-control invariant with direct fund-flow impact: disallowed counterparties can drain LP liquidity at oracle prices on pools that were designed to be restricted. Severity: **High** (allowlist bypass enabling unauthorized trading on curated pools).

## Likelihood Explanation
Exploitation requires only that the pool admin has allowlisted the router (a necessary step for any router-mediated swap to work on that pool). No special permissions, flash loans, or multi-block setup are needed. Any user with tokens can call `exactInputSingle` on the router targeting the restricted pool. The attack is repeatable every block.

## Recommendation
The extension must check the economically relevant actor, not the immediate `pool.swap` caller. Options:
- Pass the originating user through `extensionData` and have the router sign or encode it, then verify in the extension (requires trust in the router).
- Have the pool expose a separate `swapFrom(address originator, ...)` entry point that the router calls, forwarding the true user.
- Alternatively, document that `SwapAllowlistExtension` only gates direct pool callers and provide a separate router-aware allowlist extension that reads originator identity from a trusted router-set transient slot.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Admin allowlists the router (required for router swaps)
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// 3. Admin does NOT allowlist attacker
// assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

// 4. Attacker bypasses allowlist via router
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// Swap succeeds — allowlist bypassed because extension sees router, not attacker
```