### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Router Address Replaces User Identity in `beforeSwap` Hook - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap` is called with `msg.sender = router`, so the hook checks the router's address — not the original user's address — against the allowlist. Any pool admin who allowlists the router to enable router-mediated swaps for their approved users simultaneously opens the gate for every unprivileged user to bypass the allowlist by routing through the same public contract.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is on the allowlist keyed by the pool (`msg.sender` inside the extension):

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
``` [3](#0-2) 

The pool sees `msg.sender = router`. The hook therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

For any allowlisted user to swap via the router, the pool admin must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, regardless of whether that caller is on the allowlist. The router is a public, permissionless contract — any address can call it.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the economic beneficiary of the LP shares), which the pool passes through unchanged regardless of the entry path. [4](#0-3) 

### Impact Explanation

**High** — The swap allowlist is the primary access-control mechanism for curated pools. Once the router is allowlisted (a necessary step for any allowlisted user to trade via the standard periphery), the invariant "only approved addresses may swap" is completely broken. Any unprivileged user can execute swaps on a restricted pool, potentially draining LP value through oracle-priced trades that the pool admin intended to restrict to trusted counterparties.

### Likelihood Explanation

**High** — The `MetricOmmSimpleRouter` is the standard, publicly deployed swap entry point. Pool admins who want their allowlisted users to use the router (the normal UX path) must allowlist the router address. This is a routine operational step, not an edge case. Once taken, the bypass is trivially reachable by any user.

### Recommendation

The extension must recover the original user's identity rather than trusting the `sender` argument, which reflects only the immediate caller of `pool.swap`. Two sound approaches:

1. **Check `sender` only for direct pool calls; require the router to forward the original user's address in `extensionData`** — the extension decodes the real user from `extensionData` when `sender` is a known router.
2. **Gate on `recipient` instead of `sender`** — for swap allowlists the economically relevant actor is the recipient of the output token; this is harder to spoof through a router.
3. **Require direct pool interaction** — document that allowlisted pools must not be used through the router, and enforce this by checking `sender` is not a known router address.

The cleanest fix is option 1: the router passes `msg.sender` (the original user) inside `extensionData`, and the extension decodes and checks that address when the immediate `sender` is a recognized router.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  allowedSwapper[pool][alice] = true          // alice is approved
  allowedSwapper[pool][router] = true         // router allowlisted so alice can use it

Attack (bob, not on allowlist):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      pool,
      recipient: bob,
      ...
  })

  → router calls pool.swap(bob, ...)
  → pool calls _beforeSwap(router, bob, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → checks allowedSwapper[pool][router] → true
  → swap executes for bob despite bob never being allowlisted
``` [5](#0-4) [1](#0-0) [6](#0-5)

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
