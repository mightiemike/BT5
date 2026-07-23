### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees `msg.sender` = router, so the extension checks the router's allowlist entry rather than the actual user's. Any pool admin who allowlists the router to permit router-mediated swaps simultaneously grants every unpermissioned user a bypass path through that same router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

`sender` is populated by `MetricOmmPool.swap`, which passes `msg.sender` — the immediate caller of the pool function: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` the pool observes: [3](#0-2) 

For multi-hop `exactInput`, every hop after the first also uses the router as the direct pool caller: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` — a single boolean that covers every user who routes through the router — rather than `allowedSwapper[pool][actualUser]`. The pool admin faces an inescapable dilemma:

- **Router not allowlisted**: router-mediated swaps revert for everyone, including legitimately allowlisted users. The router is unusable on this pool.
- **Router allowlisted**: every unpermissioned user bypasses the allowlist by calling the router instead of the pool directly.

There is no mechanism in the hook call chain to thread the original EOA identity through the router into the extension check.

### Impact Explanation

A `SwapAllowlistExtension`-gated pool is intended to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, protocol-owned addresses, or whitelisted market makers). Once the router is allowlisted — a necessary step for any router-mediated liquidity flow — the restriction is completely defeated. Any address can execute swaps against the pool, draining LP value at oracle-derived prices without the admin's consent. This is a direct loss-of-funds / broken-core-functionality impact on curated pools.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the periphery. Pool admins who deploy a `SwapAllowlistExtension` and also want their LPs to be reachable via the standard router will naturally allowlist the router, triggering the bypass. The attacker needs no special privilege: a single public call to `exactInputSingle` with any `extensionData` suffices.

### Recommendation

The extension must verify the original user, not the immediate pool caller. Two sound approaches:

1. **Pass the original sender through `extensionData`**: require the router to encode `msg.sender` into `extensionData` and have the extension decode and verify it. The pool's `_beforeSwap` already forwards `extensionData` unchanged.
2. **Check `tx.origin` as a fallback identity when `sender` is a known router**: fragile but simple; better to use approach 1.
3. **Structural fix**: add an `originalSender` field to the `beforeSwap` hook signature so the pool can propagate the true initiator independently of the immediate caller.

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension (beforeSwap hook)
  admin allowlists: allowedSwapper[pool][alice] = true
  admin allowlists: allowedSwapper[pool][router] = true   ← required for router use

Attack (bob, not allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
     → pool.swap msg.sender = router
  3. pool._beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...)
     checks: allowedSwapper[pool][router] == true  ✓  → passes
  5. Swap executes; bob receives tokens from the curated pool.

Direct call (bob, not allowlisted):
  1. bob calls pool.swap(...) directly
  2. pool._beforeSwap(sender=bob, ...)
  3. SwapAllowlistExtension checks: allowedSwapper[pool][bob] == false → reverts NotAllowedToSwap
```

The bypass is reachable through every router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) because all of them call `pool.swap()` with the router as `msg.sender`. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
