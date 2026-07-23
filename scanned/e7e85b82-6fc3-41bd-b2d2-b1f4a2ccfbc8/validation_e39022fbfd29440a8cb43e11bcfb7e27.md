### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the actual end user, making the per-user swap guard either fully bypassable or completely blocking for EOA swappers — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is documented as gating `swap` by individual swapper address per pool. However, the `sender` it checks is `msg.sender` of `pool.swap`, which equals the `MetricOmmSimpleRouter` address whenever an EOA routes through the router. Because EOAs cannot implement `IMetricOmmSwapCallback` and therefore cannot call `pool.swap` directly, the router is the only viable swap path for EOA users. The guard therefore checks the wrong principal in every realistic usage scenario.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` checks `sender`, which is the router:** [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

**The pool passes `msg.sender` (the direct caller) as `sender`:** [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any router entry point) calls `pool.swap(...)`, `msg.sender` inside the pool is the **router contract address**, not the EOA who initiated the trade. [3](#0-2) 

**EOAs cannot call `pool.swap` directly** because the pool immediately calls back into `msg.sender` expecting `IMetricOmmSwapCallback`: [4](#0-3) 

An EOA has no code, so this callback reverts. The router is the only viable swap path for EOA users.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the position holder, i.e., the actual user), not `sender` (the `MetricOmmPoolLiquidityAdder`): [5](#0-4) 

The asymmetry confirms the swap extension checks the wrong principal.

---

### Impact Explanation

Two irreconcilable failure modes arise for any pool deploying `SwapAllowlistExtension`:

**Mode A — Allowlist is broken (unusable swap flows):**
Admin calls `setAllowedToSwap(pool, alice, true)`. Alice routes through `MetricOmmSimpleRouter`. The extension receives `sender = router`, checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap`. Alice cannot swap despite being individually allowlisted. Because EOAs cannot call `pool.swap` directly, Alice has no alternative path. Core swap functionality is permanently unusable for her.

**Mode B — Allowlist is bypassed (unauthorized swap access):**
Admin calls `setAllowedToSwap(pool, router, true)` to unblock router users. Now `allowedSwapper[pool][router]` = `true`. Any EOA — including those the admin never intended to allowlist — can swap through the router and pass the guard. The per-user restriction is completely nullified.

Neither configuration achieves the stated purpose of the extension ("Gates `swap` by swapper address, per pool").

---

### Likelihood Explanation

Every pool that:
1. Configures `SwapAllowlistExtension` in its `BEFORE_SWAP_ORDER`, and
2. Expects EOA users to trade through `MetricOmmSimpleRouter`

is affected. This is the standard deployment pattern described in the periphery README. No attacker action is required — the mismatch is structural and triggered by normal router usage.

---

### Recommendation

Replace the `sender` check with `recipient`, or introduce a dedicated `swapInitiator` field passed through `extensionData` by the router. The cleanest fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode it, similar to how Uniswap v4 hooks receive `hookData`. Alternatively, redesign the extension to check `recipient` as the closest proxy for the actual user:

```solidity
// Instead of checking sender (the router), check recipient (the user receiving output)
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

```
// Mode A: allowlisted user cannot swap through router
1. Pool deployed with SwapAllowlistExtension in BEFORE_SWAP_ORDER
2. admin calls extension.setAllowedToSwap(pool, alice, true)
3. alice calls router.exactInputSingle({pool: pool, recipient: alice, ...})
4. router calls pool.swap(alice, ...) — msg.sender in pool = router
5. pool calls _beforeSwap(sender=router, recipient=alice, ...)
6. extension: allowedSwapper[pool][router] == false → revert NotAllowedToSwap
7. alice's swap reverts; she has no direct-call alternative as an EOA

// Mode B: non-allowlisted user bypasses guard via router
1. Same pool; admin calls extension.setAllowedToSwap(pool, router, true)
   (intending to allow router users, expecting only allowlisted users use it)
2. bob (never individually allowlisted) calls router.exactInputSingle(...)
3. router calls pool.swap(...) — msg.sender in pool = router
4. extension: allowedSwapper[pool][router] == true → passes
5. bob swaps successfully; per-user allowlist is fully bypassed
``` [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L258-263)
```text
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
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
