Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the immediate caller (router) instead of the end user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates pool swaps by checking the `sender` argument, which `MetricOmmPool.swap` populates with its own `msg.sender` — the immediate caller of the pool. When swaps are routed through `MetricOmmSimpleRouter`, the router's address is what the extension sees as `sender`. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants swap access to every user of the router, completely defeating the per-user allowlist.

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap`:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the contract calling the extension), and `sender` is the first argument passed by the pool.

**`MetricOmmPool.swap` passes its own `msg.sender` as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- immediate caller of pool.swap(), not the end user
    recipient,
    ...
);
```

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

The router is `msg.sender` from the pool's perspective. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to approved addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for their approved users.
3. Any unprivileged user — not on the allowlist — calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`, the extension checks `allowedSwapper[pool][router] == true`, and the swap proceeds.
5. The allowlist is fully bypassed for every user of the router.

**Existing guards are insufficient:** The extension has no mechanism to look through the router to the originating EOA. The `allowAllSwappers` flag is a separate bypass path. There is no `tx.origin` check or callback-based identity propagation.

## Impact Explanation
The `SwapAllowlistExtension` access control is rendered ineffective for any pool that allowlists the router. Unauthorized users gain unrestricted swap access to pools intended to be gated (e.g., for KYC/compliance, whitelist-only liquidity pools, or controlled market-making environments). This is an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses the pool admin's configured access control, which is an allowed impact per contest scope.

## Likelihood Explanation
The condition requires the pool admin to have allowlisted the router — a natural and expected action for any pool that intends to support router-mediated swaps. Once the router is allowlisted, the bypass is trivially reachable by any user with no special permissions, no capital requirements beyond the swap itself, and is fully repeatable.

## Recommendation
Pass the true originating user through the call chain. Options include:
1. Have `MetricOmmPool.swap` accept an explicit `swapper` parameter (separate from `recipient`) that the router populates with `msg.sender` before calling the pool.
2. Alternatively, have the extension check `tx.origin` as a fallback when `sender` is a known router, though this is fragile.
3. The cleanest fix is to thread the end-user address from the router into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it — but this requires the router to sign or commit to the value to prevent spoofing.

## Proof of Concept

```solidity
// Foundry test sketch
function test_allowlistBypassViaRouter() public {
    // 1. Pool admin allowlists the router (intending to allow router swaps for approved users)
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

    // 2. Unprivileged attacker (not on allowlist) swaps via router
    vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // Swap succeeds — allowlist bypassed
}
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
