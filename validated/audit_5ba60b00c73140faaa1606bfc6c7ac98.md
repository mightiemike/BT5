Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router so that legitimate users can use it, every unprivileged user who calls the router also passes the check, completely nullifying the allowlist for all router-mediated swaps.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // always the direct caller of pool.swap
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks this value against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without forwarding the original `msg.sender` as the `sender` argument:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The router stores the original caller's address in transient storage via `_setNextCallbackContext` for payment purposes only — it is never forwarded to the pool's `sender` parameter or encoded into `extensionData`. [4](#0-3) 

The result: the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. The same flaw applies to `exactInput` (all hops call `pool.swap` from the router) and `exactOutputSingle`. [5](#0-4) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict swaps to specific participants (e.g., KYC'd addresses, institutional counterparties) cannot enforce that restriction when `MetricOmmSimpleRouter` is available. Any unprivileged user can call `router.exactInputSingle(pool=curatedPool, ...)` and, if the router is allowlisted, execute a swap the pool admin intended to block. This breaks the core access-control functionality of the extension, exposing LPs to unauthorized counterparties and allowing unauthorized users to trade against oracle-anchored prices in a pool not designed for them. This constitutes broken core pool functionality and an admin-boundary break by an unprivileged path. [6](#0-5) 

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it with any pool address. The bypass requires no special privileges, no flash loans, and no complex setup. Pool admins who want allowlisted users to use the router (the standard UX path) will inevitably allowlist the router address, triggering the bypass for all users. The precondition (router being allowlisted) is a natural consequence of normal pool operation. [7](#0-6) 

## Recommendation
The `beforeSwap` hook must receive and check the economically relevant actor — the end user — not the intermediate caller. The most robust fix is to require `MetricOmmSimpleRouter` to encode the original `msg.sender` into `extensionData` before calling `pool.swap`, and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a known router. Alternatively, the pool's `swap` interface could accept an explicit `originator` parameter that the router populates with its `msg.sender`, which the extension then checks. A simpler short-term fix is to prohibit allowlisting the router address in `setAllowedToSwap` and instead require direct pool interaction for allowlisted users. [8](#0-7) 

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` so that `userA` can use the router.
4. Unprivileged `userB` (not allowlisted) calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: curatedPool,
       recipient: userB,
       zeroForOne: true,
       amountIn: X,
       ...
   }));
   ```
5. The router calls `curatedPool.swap(...)` — pool's `msg.sender` is the router.
6. `_beforeSwap` passes `sender = router` to the extension.
7. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[curatedPool][router]` → `true`.
8. The swap executes. `userB` has bypassed the allowlist with no special privileges.

A Foundry integration test can confirm this by: deploying the pool with the extension, allowlisting only `userA` and the router, then calling `router.exactInputSingle` from a `userB` address and asserting the swap succeeds. [2](#0-1)

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
