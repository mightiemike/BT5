Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual end-user, enabling full allowlist bypass via the router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is `msg.sender` inside `MetricOmmPool.swap` — the direct caller of the pool, not the actual end-user. When a pool admin allowlists `MetricOmmSimpleRouter` to enable router-mediated swaps, the extension approves every swap arriving through the router regardless of who the actual end-user is, completely nullifying the per-user access control.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called the pool. When a user routes through `MetricOmmSimpleRouter`, `sender` = router address. The check becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who calls `setAllowedToSwap(pool, address(router), true)` to enable router-mediated swaps for allowlisted users inadvertently opens the gate to every user who calls through the router. [3](#0-2) 

The inconsistency is made concrete by comparing with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks `owner` — the economically relevant identity — rather than `sender`: [4](#0-3) 

## Impact Explanation
Any non-allowlisted user can bypass the swap allowlist by routing through `MetricOmmSimpleRouter`. The intended access control is completely nullified: if the pool is restricted to KYC'd users, institutional traders, or any specific cohort, the restriction is bypassed for all router users. Swaps that should be blocked execute against pool liquidity, potentially draining LP assets or executing at oracle prices the pool admin intended to gate. Both the `allowAllSwappers` flag and the per-user `allowedSwapper` mapping become meaningless once the router is allowlisted. This matches the allowed impact gate: **admin-boundary break — factory/oracle role checks are bypassed by an unprivileged path** and **broken core pool functionality causing loss of funds or unusable swap flows**.

## Likelihood Explanation
Medium. The misconfiguration is natural and predictable: a pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a specific set of users, then calls `setAllowedToSwap(pool, router, true)` believing this enables router-mediated swaps for allowlisted users only. In reality, this allowlists the router as a single identity, opening the gate to every user who calls through it. The design of `DepositAllowlistExtension` — which correctly checks `owner` — makes the inconsistency in `SwapAllowlistExtension` even more likely to be misunderstood by pool admins.

## Recommendation
`SwapAllowlistExtension` must gate the actual end-user, not the direct pool caller. Two viable approaches:
1. **`extensionData` forwarding**: The router encodes the originating user address into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply the correct address.
2. **Separate per-router allowlist**: Require that the router itself verify user allowlist status before calling the pool, and have the extension check a flag in `extensionData` attesting to that verification.

The current design where `sender` equals the pool's `msg.sender` is fundamentally incompatible with router-mediated access control.

## Proof of Concept
```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension

// Pool admin allowlists the router (intending to allow router-mediated swaps for allowlisted users)
vm.prank(poolAdmin);
ext.setAllowedToSwap(address(pool), address(router), true);

// Verify alice (non-allowlisted) is NOT in the allowlist
assertFalse(ext.allowedSwapper(address(pool), alice));

// Alice routes through the router — pool sees msg.sender == router
// Extension checks allowedSwapper[pool][router] == true → passes
vm.prank(alice);
router.exactInput(ExactInputParams({
    path: abi.encodePacked(token0, pool, token1),
    recipient: alice,
    amountIn: 1e18,
    amountOutMinimum: 0,
    extensionData: ""
}));
// Alice's swap succeeds despite not being in the allowlist
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
