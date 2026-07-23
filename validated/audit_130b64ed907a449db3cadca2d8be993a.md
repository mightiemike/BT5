Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing full per-user allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`, which forwards it unchanged to `SwapAllowlistExtension.beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, so the extension checks whether the **router** is allowlisted rather than whether the **user** is allowlisted. Any pool admin who allowlists the router to enable router-mediated swaps for approved users simultaneously grants every user on the router the ability to swap, completely defeating the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` at line 231 passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and gates on it:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` without forwarding the originating user's address — `msg.sender` at the router level is never passed to the pool:

```solidity
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

The same wrong-actor binding applies to `exactOutputSingle` (line 136) and every hop of `exactInput` (line 104) and `exactOutput` (line 165). [4](#0-3) 

When the router calls `pool.swap`, `msg.sender` inside the pool is the router. The pool passes the router address as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][real_user]`. There is no existing guard that recovers the originating user's identity — `extensionData` is passed through unchanged from the router caller and is not validated by the extension. [5](#0-4) 

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the only way to permit router-mediated swaps for approved users) simultaneously grants every user on the router the ability to swap. Any non-allowlisted address can call `router.exactInputSingle(...)` and trade on a pool that was intended to be restricted, accessing oracle-anchored liquidity that the pool admin expected only approved counterparties to reach. This constitutes a broken core pool access-control mechanism causing potential direct loss of LP assets at oracle-anchored prices to unapproved counterparties — a High severity impact under the allowed impact gate. [6](#0-5) 

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the standard publicly deployed periphery entry point callable by any user. The bypass requires only that the pool admin has allowlisted the router — which is the exact configuration any admin must use to allow approved users to trade via the router. There is no in-protocol mechanism to simultaneously allowlist the router and restrict individual users through it. The attack requires no special privileges, no flash loans, and no unusual token behavior. [7](#0-6) 

## Recommendation
The extension must check the economically relevant actor, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension and must be enforced consistently across all router entry points.

2. **Add an `originator` field to the `beforeSwap` hook interface**: The pool records `tx.origin` or the router passes it as a dedicated argument, and the extension checks that field instead of `sender`.

The `DepositAllowlistExtension` already demonstrates the correct pattern: it checks `owner` (the LP position owner, not `msg.sender` of the pool), which remains the real user even when a router or intermediary is the direct caller. [8](#0-7) 

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice is approved
  bob is NOT in the allowlist

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(params.recipient, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob despite bob not being allowlisted

Result:
  bob swaps on a curated pool he was never approved for.
  The per-user allowlist is bypassed entirely for any router-mediated swap.
  Same path works for exactOutputSingle, exactInput, and exactOutput.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, configure as above, call `router.exactInputSingle` from an address not in the allowlist, assert the swap succeeds (no `NotAllowedToSwap` revert). [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
