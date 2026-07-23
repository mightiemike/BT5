Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. This creates an irreconcilable conflict: either allowlisted users cannot use the router, or the pool admin must allowlist the router address — which then lets every unprivileged user bypass the guard by routing through the same public router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` from the pool's perspective. The original user's address is stored only in transient callback context via `_setNextCallbackContext` for payment purposes and is never forwarded to the extension: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. For `exactInput`, intermediate hops use `address(this)` (the router itself) as the payer: [4](#0-3) 

There is no mechanism in the extension call path to recover the original initiator. The `extensionData` bytes are user-supplied and cannot be trusted as an identity source. No existing guard in `SwapAllowlistExtension` distinguishes between a direct caller and a router-mediated caller.

## Impact Explanation
Any unprivileged user can trade on a curated pool intended to be restricted to a specific allowlist. The pool admin's access-control policy is completely nullified for all router-mediated swaps. This is a direct admin-boundary break: an unprivileged actor bypasses a pool admin-configured access control gate. The pool executes the swap at oracle-derived prices, so the attacker receives real token output at the pool's bid/ask. This constitutes a policy bypass with fund-impacting consequences for the pool's LP base and any downstream protocol logic that depends on curated membership.

## Likelihood Explanation
Likelihood is high. `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`, which simultaneously opens the pool to all users. The attacker needs no special privileges, no unusual token behavior, and no multi-transaction setup — a single `exactInputSingle` call suffices. [5](#0-4) 

## Recommendation
The `sender` identity forwarded to extensions must reflect the original user, not the intermediate router. Two complementary fixes:

1. **Router-side**: Store the original `msg.sender` in transient storage alongside the callback context and expose it via a standardized field in `extensionData` or a dedicated transient slot that extensions can read.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should check the `sender` parameter only when `sender` is not a known trusted router; for trusted routers it should read the original initiator from a router-provided field in `extensionData`.

The cleanest long-term fix is for the pool to pass the original initiator as a separate argument to extensions, distinct from the immediate `msg.sender`, so that allowlist guards always gate the economically relevant actor.

## Proof of Concept
```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls:
       swapAllowlist.setAllowedToSwap(pool, userA, true);
       // Admin must also allowlist the router so userA can use it:
       swapAllowlist.setAllowedToSwap(pool, address(router), true);
3. Non-allowlisted userB calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool: pool,
           recipient: userB,
           zeroForOne: true,
           amountIn: 1000,
           ...
       }));
4. Inside pool.swap(), msg.sender == address(router).
5. _beforeSwap passes sender = address(router) to SwapAllowlistExtension.
6. Extension evaluates: allowedSwapper[pool][router] == true → passes.
7. userB receives token output despite never being allowlisted.
``` [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
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
```
