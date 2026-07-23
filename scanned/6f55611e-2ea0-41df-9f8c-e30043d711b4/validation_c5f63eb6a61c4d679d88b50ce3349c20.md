### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` at the pool call boundary. When users swap through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual end user. If the pool admin allowlists the router address (a natural action to enable router-mediated swaps), every user on the network can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check: [1](#0-0) 

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

Here `msg.sender` is the pool (correct) and `sender` is the first argument the pool forwarded. The pool always forwards its own `msg.sender` as `sender`: [2](#0-1) 

So when a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ...)          // msg.sender = router
     → _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
``` [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same identity collapse occurs for `exactInput`, `exactOutputSingle`, and `exactOutput` multi-hop paths. [4](#0-3) 

**Bypass path**: A pool admin who wants to allow some router users to swap will allowlist the router address (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router, regardless of who the actual end user is. Any unprivileged address can then call `exactInputSingle` and the allowlist check passes unconditionally.

**Blocking path (secondary)**: Conversely, if the admin allowlists individual user addresses but not the router, those users cannot swap through the router at all — the check sees the router address and reverts — even though they are individually permitted. This makes the allowlist incompatible with the primary periphery entry point.

---

### Impact Explanation

A restricted pool (e.g., a private institutional pool or a pool gated to KYC'd addresses) that allowlists the router to support standard UX loses all per-user access control. Any unprivileged address can execute swaps against the pool's LP assets at oracle-derived prices, draining token0 or token1 from LPs who deposited under the assumption that only approved counterparties could trade. This is a direct loss of LP principal and breaks the core pool access invariant.

---

### Likelihood Explanation

Allowlisting the router is the natural and expected admin action to enable normal user flows. The `SwapAllowlistExtension` is a production periphery contract, and the `MetricOmmSimpleRouter` is the primary swap entry point. Any pool that deploys the allowlist extension and wants router-mediated swaps will trigger this condition. The attacker requires no special privilege — only the ability to call the public router.

---

### Recommendation

The extension must verify the actual end user, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the real user address into `extensionData` for each hop; the extension decodes and checks that address. The pool already forwards `extensionData` verbatim to every hook.
2. **Dedicated router field**: Add a `realSender` field to the `beforeSwap` hook signature so the pool can pass both `msg.sender` (the immediate caller) and an authenticated originator.

The deposit allowlist correctly avoids this problem by checking `owner` (the position beneficiary) rather than `sender` (the caller), which is why `DepositAllowlistExtension` is not affected. [5](#0-4) 

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — the natural action to enable router swaps.
3. `blockedUser` (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Pool calls `_beforeSwap(msg.sender=router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
6. `blockedUser` successfully swaps against LP assets despite never being individually permitted.
7. Repeat for any number of unprivileged addresses — all bypass the allowlist identically. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
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
