Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to its own `msg.sender` — the router contract — when swaps are routed through `MetricOmmSimpleRouter`. A pool admin who allowlists the router to enable router-mediated swaps for permitted users simultaneously grants every unpermitted address the ability to bypass the allowlist by calling the same router.

## Finding Description
`SwapAllowlistExtension.beforeSwap()` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool. The pool sets that argument to its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle()` executes, it calls `pool.swap(params.recipient, ...)` directly — the router is `msg.sender` to the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

The router never passes the originating user's address to the pool. The pool therefore forwards `sender = router_address` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

`DepositAllowlistExtension` avoids this problem by checking `owner` (the position beneficiary, explicitly passed by the caller), not `sender`:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
``` [4](#0-3) 

`SwapAllowlistExtension` has no equivalent identity anchor for the actual user.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (a necessary step to let permitted users trade via the router) simultaneously grants every unpermitted address the ability to swap in that pool. The allowlist is completely defeated for all router-mediated swaps. Unauthorized traders can execute swaps against LP positions in a pool designed to restrict access, causing curation failure and potential LP value leakage if the pool's design relied on counterparty selection.

## Likelihood Explanation
The trigger is a routine admin action: allowlisting the router so that permitted users can access the pool through the standard periphery path. Any admin who wants to support both direct and router-based swaps for a curated pool will hit this condition. The bypass requires no special privileges — any address can call `MetricOmmSimpleRouter.exactInputSingle()`. [5](#0-4) 

## Recommendation
The `beforeSwap` hook receives `extensionData` as its last argument. The router already forwards the caller-supplied `params.extensionData` unchanged to the pool. The extension should require the actual user identity to be encoded in `extensionData` (e.g., as a signed attestation or a plain address when the pool trusts the router to supply it honestly). Alternatively, the router could be extended to encode `msg.sender` into a dedicated field of `extensionData` so the extension can verify the originating user rather than the intermediary. A simpler short-term fix is to document that `SwapAllowlistExtension` cannot enforce per-user policies for router-mediated swaps and that allowlisting the router is equivalent to `allowAllSwappers = true`.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — intending only `alice` to trade.
3. Admin calls `setAllowedToSwap(pool, router, true)` — intending to let `alice` use the router.
4. `charlie` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: charlie, ...})`.
5. Router calls `pool.swap(charlie, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `charlie`'s swap executes successfully despite never being allowlisted. [6](#0-5) [2](#0-1) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-40)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```
