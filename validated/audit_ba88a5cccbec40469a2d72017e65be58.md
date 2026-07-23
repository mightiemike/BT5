Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address as `sender` Instead of End-User, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against a per-pool allowlist. However, `sender` is sourced from `msg.sender` of the `pool.swap(...)` call, which resolves to `MetricOmmSimpleRouter`'s address when users route through the periphery. A pool admin who allowlists the router — the only way to permit any router-mediated swap — inadvertently grants swap access to every user, including those explicitly excluded from the allowlist.

## Finding Description

**Root cause — how `sender` is bound:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- caller of pool.swap(), i.e. the router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim as the `sender` argument to every registered extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)  // sender = router address
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool (correct), `sender` = router address (wrong — should be the end-user).

**Exploit flow:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists only Alice and Bob: `allowedSwapper[pool][alice] = true`, `allowedSwapper[pool][bob] = true`.
2. To allow Alice and Bob to also swap through `MetricOmmSimpleRouter`, the admin must add the router: `allowedSwapper[pool][router] = true`.
3. Charlie (not on the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. The router calls `pool.swap(...)` — `msg.sender` to the pool is the router.
5. Pool calls `_beforeSwap(router_address, ...)`.
6. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router_address]` → `true` → swap proceeds.
7. Charlie successfully swaps despite being explicitly excluded.

**Why existing guards fail:**

The `onlyPool` modifier on `beforeSwap` (inherited from `BaseMetricExtension`) only verifies that the caller is a registered pool — it does not validate that `sender` reflects the true end-user. There is no mechanism in the extension or the pool to pass the original EOA through the router hop.

## Impact Explanation
The `SwapAllowlistExtension` is rendered completely ineffective for router-mediated swaps. Any unprivileged user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. This breaks the core access-control invariant of the extension — that only explicitly allowlisted addresses may swap — and constitutes an admin-boundary break where an unprivileged path (the public router) circumvents the pool admin's configured restrictions. The pool admin has no way to selectively permit some users through the router while blocking others; the choice is binary: allowlist the router (all users pass) or don't (no router swaps work).

## Likelihood Explanation
Any user aware of the router can exploit this trivially and repeatably with no special privileges. The only precondition is that the pool admin has allowlisted the router to enable legitimate router-mediated swaps, which is the expected operational setup. The attack requires a single standard router call.

## Recommendation
Pass the true originating user through the call chain. One approach: have `MetricOmmSimpleRouter` encode `msg.sender` (the end-user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a known router. A cleaner fix is to add a dedicated `swapper` field to the pool's `swap()` interface (separate from `recipient`) that the router populates with `msg.sender`, and have `_beforeSwap` forward this as the checked identity. Alternatively, restrict the allowlist check to `tx.origin` as a stopgap (with known limitations), or require the router to be non-allowlistable and instead gate at the router level per-user.

## Proof of Concept

```solidity
// Foundry test sketch
function test_allowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension, only router allowlisted
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
    // Charlie is NOT on the allowlist
    assertFalse(swapAllowlist.isAllowedToSwap(address(pool), charlie));

    // Charlie routes through the router — sender seen by extension = router address
    vm.prank(charlie);
    // Should revert but does not:
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        recipient: charlie,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // Charlie successfully swapped despite not being on the allowlist
}
```