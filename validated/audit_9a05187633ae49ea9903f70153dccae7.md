### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router**, not the end-user. If a pool admin allowlists the router address (a natural step to enable router-mediated swaps for allowlisted users), every unprivileged user can bypass the per-user swap gate by routing through the public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the economic actor
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` (the direct caller) is allowlisted for the pool (`msg.sender` of the extension call = the pool):

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

Here `msg.sender` of `pool.swap()` is the **router contract**, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This is structurally asymmetric with `DepositAllowlistExtension`, which correctly gates on `owner` (the economic actor / position owner), not `sender` (the payer/caller):

```solidity
// DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The pool's own NatDoc acknowledges the operator pattern for deposits: "`msg.sender` pays but need not equal `owner`." No equivalent protection exists for swaps.

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., trusted market makers, KYC'd users, or institutional partners) and also allowlists the `MetricOmmSimpleRouter` address (to let those users trade via the router) inadvertently opens the gate to **all** users. Any unprivileged address can call `router.exactInputSingle()` or `router.exactInput()`, causing the extension to see `sender = router` (allowlisted) and pass the guard. The pool's curation policy is silently nullified, and non-allowlisted users can execute swaps against LP capital that was deployed under the assumption of a restricted counterparty set.

### Likelihood Explanation

The scenario is reachable through normal, unprivileged user actions. The only prerequisite is that the pool admin allowlists the router — a natural and expected configuration step when the admin wants allowlisted users to be able to use the standard periphery. The admin has no on-chain signal that doing so opens the gate to all router users, because the deposit allowlist (which the admin likely uses as a mental model) correctly gates on the economic actor regardless of the caller. Likelihood is **Medium**.

### Recommendation

`SwapAllowlistExtension.beforeSwap()` should gate on the `recipient` argument (the address that receives output tokens and is the economic beneficiary of the swap) rather than `sender` (the direct caller), mirroring how `DepositAllowlistExtension` gates on `owner` rather than `sender`. Alternatively, the pool's NatDoc and extension documentation should explicitly warn that allowlisting the router grants access to all router users, and the router should forward the original `msg.sender` as an additional argument or via `extensionData` so extensions can gate on the true initiator.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  alice  → allowlisted swapper
  router → allowlisted swapper (admin adds this so alice can use the router)
  bob    → NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; bob trades on a pool he was never meant to access

Result:
  bob bypasses the per-user allowlist by routing through the public router.
  LP capital deployed under a restricted-counterparty assumption is now
  accessible to any unprivileged address.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
