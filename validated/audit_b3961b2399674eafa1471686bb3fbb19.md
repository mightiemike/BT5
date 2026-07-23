Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router address (a necessary step to permit any router-mediated swap on a curated pool), every unprivileged user can bypass the per-user allowlist by routing through the router.

## Finding Description

**Root cause — wrong identity in `beforeSwap`:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)` at line 231, forwarding its own `msg.sender` as the `sender` argument:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap(), NOT the end user
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap()` relays this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...))   // sender == pool's msg.sender
);
```

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` is allowlisted for the calling pool (`msg.sender` == pool):

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**Router path — identity substitution:**

`MetricOmmSimpleRouter.exactInputSingle()` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,
  params.zeroForOne,
  MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
  priceLimitX64,
  "",
  params.extensionData
);
```

`msg.sender` at the pool is the router contract. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Exploit flow:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` in the `beforeSwap` order.
2. Admin allowlists specific users: `setAllowedToSwap(pool, alice, true)`.
3. Admin also allowlists the router so that `alice` can use the router: `setAllowedToSwap(pool, router, true)`.
4. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` — pool sees `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. Attacker's swap executes successfully, bypassing the per-user allowlist.

**Existing guards are insufficient:** `BaseMetricExtension.onlyPool` only verifies that the caller of `beforeSwap` is a registered pool — it does not verify the identity of the end user. There is no mechanism inside the extension to recover the original `msg.sender` of the router call.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict trading to a specific set of addresses can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker trades against LP funds on a pool that was explicitly configured to deny them access. This constitutes a broken core pool functionality / admin-boundary break causing direct exposure of LP assets to unauthorized counterparties, meeting the High severity threshold.

## Likelihood Explanation
The bypass requires only that the pool admin has allowlisted the router address — a natural and expected action for any curated pool whose permitted users are expected to interact via the standard periphery router. The attacker needs no special privileges, no capital beyond normal swap amounts, and can repeat the bypass on every block. The condition is reachable on any production curated pool that supports router usage.

## Recommendation
The extension must gate the economically relevant actor, not the proximate caller. Two sound approaches:

1. **Pass the original initiator through the router:** Have `MetricOmmSimpleRouter` encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap()` decode and check it when the direct `sender` is a known router. This requires a trusted router registry or a signed initiator field.

2. **Check `sender` only when it is not a trusted router; otherwise check the decoded initiator:** Add a `trustedRouter` mapping to `SwapAllowlistExtension`; if `sender` is a trusted router, decode the real user from `extensionData` and check that address instead.

3. **Require direct pool calls for allowlisted pools:** Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router address, and instead require allowed users to call the pool directly.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // Setup: curated pool, alice allowlisted, router allowlisted for alice's convenience
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Attacker (not allowlisted) routes through the router
    address attacker = makeAddr("attacker");
    token0.mint(attacker, 1_000);
    vm.startPrank(attacker);
    token0.approve(address(router), type(uint256).max);

    // This should revert with NotAllowedToSwap but does NOT
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: attacker,
        deadline: block.timestamp + 1,
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: ""
    }));
    vm.stopPrank();
    // Attacker successfully swapped despite not being individually allowlisted
}
```