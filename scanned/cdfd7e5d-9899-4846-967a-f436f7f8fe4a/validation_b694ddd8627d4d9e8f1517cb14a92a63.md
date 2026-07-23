The call chain is fully traceable. Here is the exact flow and determination:

**Call chain:**

1. Blocked EOA calls `MetricOmmSimpleRouter.exactOutputSingle(params)` — `msg.sender` = EOA
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — `msg.sender` to pool = **router**
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — passes **router** as `sender`
4. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` is called with `msg.sender` = pool

The check in `SwapAllowlistExtension.beforeSwap`:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

evaluates as `allowedSwapper[pool][router]` — **never the EOA**. [1](#0-0) [2](#0-1) 

---

### Title
SwapAllowlistExtension sender is always the router, not the EOA, allowing any non-allowlisted user to bypass per-pool swap gating via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, that `sender` is always the router contract address, not the originating EOA. Any pool admin who allowlists the router (a natural configuration to enable router-based swaps) inadvertently grants every EOA — including explicitly blocked ones — the ability to swap on the curated pool.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
``` [3](#0-2) 

When called via `MetricOmmSimpleRouter.exactOutputSingle`, `msg.sender` is the router, so `sender = router`. The extension then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [4](#0-3) 

`msg.sender` here is the pool, `sender` is the router. The EOA is never consulted. If `allowedSwapper[pool][router] == true`, the check passes for **any** EOA regardless of their individual allowlist status.

The same flaw applies to `exactInputSingle`, `exactInput`, and `exactOutput` — all router entry points pass `msg.sender` (the router) as the payer/sender identity to the pool. [5](#0-4) [6](#0-5) 

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses provides no actual restriction when the router is allowlisted. Any EOA can call `exactOutputSingle` through the router and receive a precise token amount from the pool. This breaks the core curation invariant of the extension and constitutes broken core pool functionality with direct fund-flow impact (unauthorized parties extract tokens from a pool intended to be restricted).

### Likelihood Explanation
The router is the primary user-facing swap interface. A pool admin enabling router-based swaps by allowlisting the router address is the expected operational pattern. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a standard `exactOutputSingle` call from any EOA.

### Recommendation
The extension must verify the true economic actor, not the intermediary. Options:

1. **Pass the original initiator through the call chain**: Have the router store `msg.sender` in transient storage and expose it; the pool or extension reads it to identify the true swapper.
2. **Require direct pool interaction for allowlisted pools**: Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router; instead, allowlist individual EOAs who call the pool directly.
3. **Extension-level EOA recovery**: Allow the extension to read an authenticated initiator from `extensionData` signed or set by the router, though this requires router cooperation and careful replay protection.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, router allowlisted, EOA blocked
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// blockedEOA is NOT in allowedSwapper

// Attack: blocked EOA calls exactOutputSingle through router
vm.prank(blockedEOA);
uint256 amountIn = router.exactOutputSingle(
    IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: 1000,
        amountInMaximum: type(uint128).max,
        recipient: blockedEOA,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Swap succeeds; blockedEOA receives exactly 1000 token1
// allowedSwapper[pool][blockedEOA] == false, but check was against router
assertEq(token1.balanceOf(blockedEOA), 1000);
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
