### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end user, allowing any unprivileged caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is the production gate that restricts which addresses may swap in a pool. Its `beforeSwap` hook checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router address (a natural step to enable router-based swaps for their allowlisted users), every unprivileged address on the network can bypass the per-user restriction by calling the router, because the extension sees only the router and approves it unconditionally.

---

### Finding Description

**Root cause — wrong identity checked in `SwapAllowlistExtension.beforeSwap`:** [1](#0-0) 

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

`msg.sender` here is the pool (correct). `sender` is whatever address called `pool.swap()`. The pool passes `msg.sender` of its own `swap()` call as `sender`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`: [3](#0-2) 

So `sender` = router address. The extension checks `allowedSwapper[pool][router]`. If the router is allowlisted, the check passes for **every user** who calls the router, regardless of whether that user is individually allowlisted.

**Contrast with `DepositAllowlistExtension`**, which correctly gates on `owner` (the LP position owner — the economically relevant party), not `sender` (which could be the `MetricOmmPoolLiquidityAdder`): [4](#0-3) 

The asymmetry is the bug: deposits gate the right identity (`owner`); swaps gate the wrong identity (`sender` = intermediary router).

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool and allowlists the router address (to let their approved users trade via the router) inadvertently opens the pool to every address on the network. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput`, which calls `pool.swap()` with `sender = router`. The extension approves the call because `allowedSwapper[pool][router] == true`. The per-user allowlist is completely nullified for router-mediated swaps.

Concrete consequence: the pool admin cannot simultaneously (a) allow their approved users to use the router and (b) block non-approved users from using the router. Allowlisting the router is all-or-nothing. Non-approved users gain full swap access to a pool that was designed to be restricted, trading against LP positions whose owners expected only vetted counterparties.

---

### Likelihood Explanation

The trigger is a valid, non-malicious admin action: allowlisting the router so that approved users can use the standard periphery. This is the expected operational pattern for any pool that wants to support the router while maintaining an allowlist. The bypass is then reachable by any unprivileged address with no special permissions, no flash loan, and no oracle manipulation — a single call to `exactInputSingle` suffices.

---

### Recommendation

Gate on the actual end user, not the intermediary. Two complementary approaches:

1. **Check `extensionData`**: Have the router encode `msg.sender` (the actual user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. This requires a convention between the router and the extension.

2. **Structural fix (preferred)**: Mirror the deposit extension's design. For swaps the economically relevant actor is the payer (the address that will supply input tokens). The router stores the payer in transient storage; expose it via a standardized interface so the extension can read it. Alternatively, add a `payer` field to the `beforeSwap` hook signature (a breaking change to `IMetricOmmExtensions`).

At minimum, document clearly that allowlisting the router grants swap access to all router users, so pool admins do not allowlist it expecting per-user granularity.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is approved
  allowedSwapper[pool][router] = true   // admin adds router so alice can use it
  allowedSwapper[pool][bob]    = false  // bob is NOT approved

Attack (bob):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient=bob, ...) — msg.sender = router
  3. pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...)
  4. extension checks allowedSwapper[pool][router] → true → no revert
  5. bob's swap executes successfully despite not being on the allowlist

Direct call (bob, for comparison):
  1. bob calls pool.swap(...) directly — msg.sender = bob
  2. pool calls SwapAllowlistExtension.beforeSwap(sender=bob, ...)
  3. extension checks allowedSwapper[pool][bob] → false → NotAllowedToSwap revert
```

The bypass is reachable on every `exactInput*` and `exactOutput*` entry point of `MetricOmmSimpleRouter` because all of them call `pool.swap()` with `msg.sender = router`. [5](#0-4) [6](#0-5) [1](#0-0)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
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
