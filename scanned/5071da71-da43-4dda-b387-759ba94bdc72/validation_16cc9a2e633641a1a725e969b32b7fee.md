### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — which is `msg.sender` of `pool.swap()` — against the per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently opens the pool to every user, completely defeating the allowlist.

### Finding Description

**Root cause — wrong actor checked in the guard**

In `MetricOmmPool.swap()`, the value forwarded as `sender` to every extension hook is `msg.sender` of the `swap()` call: [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput()`) is used, the router is `msg.sender` of `pool.swap()`, so `sender = router`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the router. The effective check is `allowedSwapper[pool][router]`. [3](#0-2) 

**The dilemma this creates for pool admins**

| Admin configuration | Direct swap by allowlisted user | Router swap by allowlisted user | Router swap by non-allowlisted user |
|---|---|---|---|
| Allowlist users only (not router) | ✓ passes | ✗ reverts (router not listed) | ✗ reverts |
| Allowlist users + router | ✓ passes | ✓ passes | ✓ passes — **bypass** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

**Contrast with `DepositAllowlistExtension`**

The deposit guard correctly ignores `sender` and checks `owner` — the economic beneficiary of the LP position — so it remains effective regardless of whether the `MetricOmmPoolLiquidityAdder` is used as an intermediary: [4](#0-3) 

The swap guard has no equivalent: it checks the intermediary (`sender = router`) rather than the economic actor (`recipient`), making it structurally bypassable through the supported periphery path.

### Impact Explanation
Any user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. Pools restricted to prevent adverse selection (e.g., only trusted market makers) or to enforce access control will have that restriction silently removed for all router-mediated swaps. Non-allowlisted users can drain LP value through unfavorable trades that the allowlist was specifically deployed to prevent — direct loss of LP principal.

### Likelihood Explanation
Pool admins who deploy a `SwapAllowlistExtension` pool and also want to support the standard router will naturally allowlist the router address. This is the expected operational configuration; the bypass is then trivially reachable by any user with no special privileges, no malicious setup, and no non-standard tokens.

### Recommendation
Check the actual end-user rather than the direct caller of `pool.swap()`. The second parameter of `beforeSwap` is `recipient` — the address that receives output tokens — which is set to the end-user by the router: [5](#0-4) 

Changing the guard to check `recipient` instead of `sender` mirrors the pattern already used correctly in `DepositAllowlistExtension` (checking `owner` rather than `sender`), and ensures the allowlist gates the economically relevant actor regardless of which supported periphery path is used.

```diff
- function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
+ function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
          revert IMetricOmmPoolActions.NotAllowedToSwap();
      }
      return IMetricOmmExtensions.beforeSwap.selector;
  }
```

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only approved swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Charlie (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: charlie, ...})`.
5. Router calls `pool.swap(recipient=charlie, ...)` — pool sees `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, recipient=charlie, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → hook passes.
8. Charlie's swap executes on the supposedly restricted pool, bypassing the allowlist entirely. [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L227-241)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
