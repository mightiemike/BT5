The call path is fully traceable. Here is the determination:

**The vulnerability is real.**

## Exact Call Trace

**Step 1 — User calls router:**
`user` → `MetricOmmSimpleRouter.exactOutputSingle(...)` (`msg.sender = user`)

**Step 2 — Router calls pool:**
`MetricOmmSimpleRouter` → `pool.swap(recipient, ...)` (`msg.sender` to pool = **router**)

**Step 3 — Pool calls `_beforeSwap` with `msg.sender`:** [1](#0-0) 

`sender` passed to `_beforeSwap` is `msg.sender` = **router**, not the original user.

**Step 4 — Extension checks `allowedSwapper[pool][router]`:** [2](#0-1) 

`msg.sender` inside `beforeSwap` is the pool; `sender` is the router. The check is `allowedSwapper[pool][router]`.

**Step 5 — Router calls pool with no user identity forwarded:** [3](#0-2) 

The original `msg.sender` (the user) is never passed to `pool.swap`. The router is the only identity the pool sees.

---

## Result

### Title
SwapAllowlistExtension Bypassed via Router: `sender` Is Router, Not End-User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the direct caller is the router, not the user. If the router is allowlisted (a natural admin action to enable router-mediated swaps), every user — including non-allowlisted ones — can bypass the swap gate.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- this is the router, not the end-user
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

where `msg.sender = pool` and `sender = router`. The end-user's address is never consulted. A pool admin who allowlists the router (intending to permit router-mediated swaps) inadvertently opens the gate to all users, because the router is a public, permissionless contract with no per-user access control. [4](#0-3) 

### Impact Explanation
The swap allowlist — the primary mechanism for pool curation — is completely ineffective for any user who routes through `MetricOmmSimpleRouter`. A curated pool that intends to restrict swaps to a specific set of users provides no restriction at all once the router is allowlisted. Non-allowlisted users can execute swaps (including exact-output swaps) and receive output tokens from the pool. This breaks core pool functionality (the allowlist gate) and constitutes a bypass of an admin-configured access control with direct fund-flow impact (non-permitted swaps execute and drain pool liquidity).

### Likelihood Explanation
High. `MetricOmmSimpleRouter` is the standard public swap interface. Pool admins who configure a swap allowlist and also want to support router-mediated swaps will naturally allowlist the router. The bypass requires no special knowledge — any user can call `exactOutputSingle` or `exactInputSingle` on the router against the curated pool.

### Recommendation
Forward the original initiator through the router to the pool, or have the extension check `tx.origin` (with appropriate caveats), or redesign the allowlist to gate at the router level by checking the caller of the router rather than the caller of the pool. The cleanest fix is for `MetricOmmSimpleRouter` to accept a `swapper` parameter and pass it as `callbackData` or `extensionData` so the extension can verify the true initiator. Alternatively, the pool admin documentation must explicitly warn that allowlisting the router grants access to all users.

### Proof of Concept
```solidity
// Foundry integration test
function test_nonAllowlistedUserBypassesSwapAllowlistViaRouter() public {
    // Pool admin allowlists only the router (not the user)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    address nonAllowlistedUser = makeAddr("attacker");
    deal(address(tokenIn), nonAllowlistedUser, 10_000);
    vm.prank(nonAllowlistedUser);
    tokenIn.approve(address(router), type(uint256).max);

    uint256 balanceBefore = tokenOut.balanceOf(nonAllowlistedUser);

    // Non-allowlisted user swaps through the router — should revert, but doesn't
    vm.prank(nonAllowlistedUser);
    router.exactOutputSingle(IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(tokenIn),
        tokenOut: address(tokenOut),
        zeroForOne: true,
        amountOut: 1000,
        amountInMaximum: 10_000,
        recipient: nonAllowlistedUser,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));

    // Assert non-allowlisted user received output — allowlist was bypassed
    assertGt(tokenOut.balanceOf(nonAllowlistedUser) - balanceBefore, 0);
}
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
