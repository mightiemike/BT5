Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks immediate caller instead of economic actor, enabling allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` becomes the router's address. Any pool admin who allowlists the router to enable normal UX for legitimate users simultaneously grants every unprivileged user the ability to bypass the allowlist entirely by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool: [1](#0-0) 

`MetricOmmPool.swap()` always passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of the pool: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual user's address (`msg.sender` of the router call) is never inspected. The admin's only mechanism to grant or revoke access is `setAllowedToSwap`, which operates on a single address dimension with no way to express "router is allowed, but only for these specific users": [4](#0-3) 

The forced dilemma: if the router is not allowlisted, legitimate allowlisted users cannot use the router at all. If the router is allowlisted, every unprivileged user bypasses the allowlist via the router. There is no intermediate configuration. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

## Impact Explanation
The allowlist's purpose — restricting pool access to specific counterparties, KYC'd users, or institutional participants — is completely defeated. Any unprivileged user gains access to restricted liquidity at oracle-anchored prices intended only for vetted counterparties. This constitutes a broken core pool functionality (the access-control invariant of curated pools) and an admin-boundary break where an unprivileged path bypasses a pool admin's access control. Unauthorized users can drain LP positions at oracle-anchored prices, front-run allowlisted participants, and trade on pools they were explicitly excluded from.

## Likelihood Explanation
The router is the protocol's primary, documented swap entrypoint providing slippage protection, multi-hop routing, and deadline checks. Any pool admin who wants allowlisted users to have normal UX must allowlist the router — this is an expected operational step, not an exotic misconfiguration. Once the router is allowlisted, the bypass requires no special access, capital, or technical sophistication beyond submitting a standard router swap call. It is repeatable by any address on every block.

## Recommendation
The allowlist must bind to the actual economic actor, not the immediate pool caller. The most robust fix is to have the router encode `msg.sender` into `extensionData`, and have the extension decode and check that address when the immediate caller is a known trusted router. The extension would verify the caller is a recognized periphery contract before trusting the `extensionData`-supplied user address, then check `allowedSwapper[pool][decodedUser]`. Alternatively, introduce a two-level mapping: `allowedCaller[pool][sender]` for direct callers and `allowedUser[pool][user]` for users decoded from `extensionData` when the caller is a trusted router.

## Proof of Concept
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` in the `beforeSwap` order.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so `userA` can use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `userB`'s swap executes at oracle-anchored prices on the curated pool, bypassing the allowlist entirely.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
