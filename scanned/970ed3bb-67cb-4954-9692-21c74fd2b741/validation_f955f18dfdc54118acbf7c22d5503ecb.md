Looking at the key files, I need to trace the actor identity through the swap allowlist guard when the router is used.

**Key observations:**

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 

2. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = direct caller of `pool.swap()` [2](#0-1) 

3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly — making the router the `msg.sender` to the pool, so `sender` = router address [3](#0-2) 

4. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` and checks `owner` (the position owner / end user), correctly handling the operator pattern [4](#0-3) 

5. The pool NatSpec explicitly documents the operator pattern for deposits: "`msg.sender` pays but need not equal `owner`" [5](#0-4) 

---

### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Swap Curation — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates on `sender`, which is the direct `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is the caller, `sender` equals the router address, not the end user. A pool admin who allowlists the router to enable router-based swaps for their curated users inadvertently opens the gate to every user who routes through the same router contract.

### Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is whatever address called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutput`, `exactOutputSingle`) is used, the router calls `pool.swap(...)` directly, so `sender` = router address.

The pool admin cannot selectively allow individual end users through the router. Their only choices are:
- **Allowlist the router address** → every user who calls the router can swap, regardless of whether they are individually curated.
- **Do not allowlist the router** → no user can swap through the router, even those who are individually allowlisted.

This is structurally inconsistent with `DepositAllowlistExtension`, which ignores `sender` and checks `owner` (the position owner / end user), correctly supporting the operator pattern. The swap extension has no equivalent end-user identifier because the swap flow has no `owner` parameter — but the consequence is that the guard is bound to the wrong actor. [2](#0-1) [3](#0-2) [4](#0-3) 

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of users (e.g., KYC'd counterparties, institutional participants) is fully bypassed for any user who routes through `MetricOmmSimpleRouter` once the admin allowlists the router. Unauthorized users can execute swaps against the pool's liquidity, causing impermanent loss to LPs and violating the pool's intended curation policy. This is an admin-boundary break: the pool admin's configured access control does not enforce the intended per-user boundary on the supported periphery swap path.

### Likelihood Explanation
The `MetricOmmSimpleRouter` is the primary supported swap entrypoint. Any pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. This is a natural and expected admin action. Once done, the bypass is available to every user unconditionally. No special privileges, flash loans, or unusual token behavior are required — a standard `exactInputSingle` call suffices.

### Recommendation
1. **Pass end-user identity through `extensionData`**: The router could encode `msg.sender` (the end user) into `extensionData`, and the extension could decode and check it. This requires a coordinated change to the router and extension.
2. **Add a `recipient`-based check option**: Expose a configuration flag that allows the extension to check `recipient` (the swap output recipient) instead of `sender`, which is closer to the end user in single-hop router flows.
3. **Document the limitation explicitly**: At minimum, document that `SwapAllowlistExtension` gates by the direct pool caller, so pool admins understand that allowlisting the router opens the gate to all router users.

### Proof of Concept
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` configured.
2. Admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — intending only Alice to swap.
3. Admin also calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — intending to let Alice use the router.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: curatedPool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender` = router.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps in the curated pool, bypassing the per-user allowlist. [6](#0-5) [1](#0-0) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```
