Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual trader — any user bypasses the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, where `sender` is `msg.sender` to the pool — the router contract — not the originating user. When a pool admin allowlists `MetricOmmSimpleRouter` to permit router-mediated swaps for approved users, every unprivileged user can bypass the allowlist by routing through the same public router. Conversely, if the router is not allowlisted, approved users cannot use the router at all, breaking the intended UX. Either outcome violates the core invariant that the allowlist gates the economically relevant swapper.

## Finding Description

**Root cause — wrong actor in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` (metric-periphery/contracts/extensions/SwapAllowlistExtension.sol, lines 31-41):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct for the mapping key). `sender` is the first argument forwarded by the pool's `swap` function, which is `msg.sender` **to the pool** — i.e., whoever called `pool.swap(...)`. When the call originates from `MetricOmmSimpleRouter`, `sender` is the router's address, not the originating EOA or contract.

**Call path:**

```
User (non-allowlisted) 
  → MetricOmmSimpleRouter.exactInput / exactOutput
    → pool.swap(recipient, ...) [msg.sender = router]
      → ExtensionCalling._beforeSwap(sender = router, ...)
        → SwapAllowlistExtension.beforeSwap(sender = router, ...)
          → allowedSwapper[pool][router] checked — NOT the user
```

**Exploit scenario:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can reach the pool via the standard router.
3. Any unprivileged user calls `MetricOmmSimpleRouter.exactInput(...)` targeting the restricted pool.
4. The extension sees `sender = router`, which is allowlisted → swap proceeds.
5. The actual user identity is never checked.

**Existing guards are insufficient:**

The `onlyPool` modifier on `beforeSwap` only verifies that the caller is a registered pool — it does not verify that the `sender` argument reflects the true originating user. The pool itself passes `msg.sender` (the router) as `sender` with no mechanism to unwrap the original caller. The `NotAllowedToSwap` error comment in the interface even states it rejects `msg.sender`, confirming the design intent was to gate the direct pool caller, which breaks when an intermediary is involved.

## Impact Explanation

An unprivileged trader can execute swaps on a curated, allowlist-restricted pool by routing through the public `MetricOmmSimpleRouter`. This is a direct bypass of an access-control mechanism protecting pool liquidity. LP funds in the restricted pool are exposed to swaps from actors the pool admin explicitly intended to exclude. This constitutes a broken core pool functionality / admin-boundary break with direct fund impact on LP positions in curated pools.

## Likelihood Explanation

The router is a standard, publicly deployed periphery contract. Any user aware of the allowlist restriction can trivially route through it. The only precondition is that the pool admin has allowlisted the router address (a natural and expected operational step to support normal router usage). No special privileges, flash loans, or complex setup are required. The attack is repeatable on every swap.

## Recommendation

Pass the originating user through the call chain rather than relying on `msg.sender` at the pool boundary. Two concrete options:

1. **Preferred — thread the originator:** Have `MetricOmmSimpleRouter` pass the original `msg.sender` as an explicit `sender` argument to `pool.swap`, and have the pool forward that value (rather than its own `msg.sender`) to extension hooks. This requires a pool-level change to accept a trusted sender from whitelisted periphery contracts.

2. **Extension-level fix:** In `SwapAllowlistExtension.beforeSwap`, decode the true originator from `extensionData` (caller-supplied and router-forwarded), and verify it against the allowlist. The router must be updated to populate this field with `msg.sender` before forwarding.

Either fix must ensure the checked identity is the economically relevant actor, not an intermediate contract.

## Proof of Concept

```solidity
// Foundry test sketch
function test_allowlistBypassViaRouter() public {
    // Pool admin allowlists the router (normal operational step)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Add liquidity from an approved depositor
    depositExtension.setAllowedToDeposit(address(pool), approvedDepositor, true);
    vm.prank(approvedDepositor);
    pool.addLiquidity(...);

    // Unprivileged attacker — NOT in the swap allowlist
    address attacker = makeAddr("attacker");
    // attacker is NOT allowlisted: swapExtension.isAllowedToSwap(pool, attacker) == false

    // Attacker routes through the public router
    vm.prank(attacker);
    router.exactInput(
        IMetricOmmSimpleRouter.ExactInputParams({
            path: abi.encodePacked(token0, pool, token1),
            recipient: attacker,
            amountIn: 1000,
            amountOutMinimum: 0,
            extensionData: new bytes[](1)
        })
    );
    // Swap succeeds — allowlist bypassed
}
```