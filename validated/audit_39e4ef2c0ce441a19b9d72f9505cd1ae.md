Audit Report

## Title
SwapAllowlistExtension checks router address instead of end user, allowing full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` is the router address, not the end user. If the pool admin allowlists the router to permit allowlisted users to reach the pool through the public router, every user—including non-allowlisted ones—bypasses the restriction entirely.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking the `sender` argument against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool: [1](#0-0) 

`MetricOmmPool.swap()` passes its own `msg.sender` (the direct caller) as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a known set of users.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can reach the pool through the public router.
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool.
4. The pool calls `_beforeSwap(router, ...)`.
5. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
6. The swap executes for a user who was never allowlisted.

The router provides no mechanism to forward the original `msg.sender` to the pool's `swap()` call, and the extension does not use `extensionData` to recover the real user identity. [5](#0-4) 

## Impact Explanation
This is an admin-boundary break: the pool admin's access-control boundary is broken by an unprivileged, publicly reachable path. Any non-allowlisted user can execute swaps on a pool that was explicitly restricted to a known set of swappers. Depending on the pool's purpose (e.g., KYC-gated, institutional-only, or whitelist-only liquidity), this allows unauthorized parties to interact with restricted liquidity, directly violating the pool admin's intended access control and constituting a High-severity finding under Sherlock's admin-boundary-break category.

## Likelihood Explanation
The bypass requires only that the pool admin has allowlisted the router—a natural and expected operational step for any pool that wants to support allowlisted users routing through the public router. The attacker needs no special privileges, no capital beyond the swap amount, and no off-chain coordination. The attack is repeatable on every swap and is reachable by any Ethereum address.

## Recommendation
The extension must verify the actual end user, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: Require the router to ABI-encode `msg.sender` into `extensionData` and have the extension decode and check it. This requires a coordinated change in both the router and the extension.
2. **Check `tx.origin` as a fallback**: When `sender` is a known router, fall back to `tx.origin` for the allowlist check. This is simpler but has known limitations with smart-contract wallets.
3. **Preferred — router-level allowlist**: Add a separate `SwapAllowlistExtension` setter that allowlists `(pool, router)` only as a relay, and require the router to pass the original caller's address as an additional argument to `pool.swap()`, with the extension reading it from `extensionData`.

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// 1. Deploy pool with SwapAllowlistExtension
// 2. Admin allowlists the router: setAllowedToSwap(pool, router, true)
// 3. Alice (not allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: alice,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// 4. pool.swap() is called with msg.sender = router
// 5. _beforeSwap(router, ...) → allowedSwapper[pool][router] = true → passes
// 6. Alice's swap executes despite never being allowlisted
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-80)
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
