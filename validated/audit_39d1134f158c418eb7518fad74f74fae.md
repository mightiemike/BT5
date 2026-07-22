The call chain is fully traceable. Here is the exact flow:

**`MetricOmmPool.swap()`** passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [1](#0-0) 

**`SwapAllowlistExtension.beforeSwap()`** checks `allowedSwapper[msg.sender][sender]` where `msg.sender = pool` and `sender = router`: [2](#0-1) 

**`MetricOmmSimpleRouter.exactInputSingle()`** calls `pool.swap()` as `msg.sender = router`, not the originating EOA: [3](#0-2) 

The vulnerability is real. The allowlist checks the immediate caller of `pool.swap()`, which is the router — not the originating EOA. If `allowedSwapper[pool][router] = true`, every EOA that routes through the public router bypasses the per-user gate entirely.

---

### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any EOA to bypass per-user swap allowlists via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, that value is the router address, not the originating EOA. Allowlisting the router therefore grants unrestricted swap access to every user of the public router.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← router address, not the EOA
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller), and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. Any EOA that calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) will have its identity collapsed to the router address before the allowlist is consulted.

### Impact Explanation
A pool admin who intends to restrict swaps to a specific set of EOAs (e.g., KYC'd addresses, protocol-owned contracts) and also allowlists the public router inadvertently opens the pool to all users. The allowlist enforcement is silently broken: non-allowlisted EOAs execute swaps at live oracle prices, draining LP-gated liquidity. This constitutes broken core pool functionality and potential direct loss of LP assets.

### Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical public swap entry point. Any pool operator who allowlists the router (a natural configuration for "allow router-based swaps") triggers the bypass. No privileged access, malicious setup, or non-standard token behavior is required — only a standard `exactInputSingle` call from an unlisted EOA.

### Recommendation
Pass the originating user through the router to the pool, or redesign the allowlist check to use `recipient` or an explicit `swapper` field carried in `extensionData`. One concrete fix: have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with the pool authenticating the router as the trusted source). Alternatively, document that allowlisting the router is equivalent to `setAllowAllSwappers = true` and enforce that invariant in the admin setter.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Call `swapExtension.setAllowedToSwap(pool, address(router), true)` — allowlist only the router.
3. From a non-allowlisted EOA, call `router.exactInputSingle(...)` targeting the pool.
4. Observe the swap succeeds: `allowedSwapper[pool][router]` is `true`, so `beforeSwap` does not revert, and the non-allowlisted EOA receives output tokens.
5. Confirm that calling `pool.swap(...)` directly from the same EOA reverts with `NotAllowedToSwap` — proving the bypass is router-specific.

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
