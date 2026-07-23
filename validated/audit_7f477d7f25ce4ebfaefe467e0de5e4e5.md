Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` Gates on Router Address Instead of Actual User, Enabling Complete Allowlist Bypass — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` parameter, which `MetricOmmPool.swap()` binds to `msg.sender` — the immediate caller of the pool. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the user. If the pool admin allowlists the router to enable router-based swaps, every non-allowlisted user can bypass the curation policy by calling any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that `sender` value directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` gates exclusively on `sender` (the first parameter), ignoring the `recipient` (the actual user): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router `msg.sender` at the pool: [4](#0-3) 

The same binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result: `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][user]`. The check never touches the actual user's address.

## Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma. If the router is allowlisted (the necessary action to enable router-based swaps), `allowedSwapper[pool][router] = true` passes for every call through the router regardless of who the actual user is — any non-allowlisted address can bypass the curation policy. The allowlist invariant is completely defeated: a non-allowlisted user receives output tokens from a pool they should never have accessed, constituting unauthorized extraction of LP assets from a restricted pool. This is a direct broken-core-functionality / admin-boundary-break impact with fund-level consequences.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard periphery swap path. A pool admin who wants router-based swaps to work at all must allowlist the router, which immediately opens the bypass to every address. No special privilege, flash loan, or unusual token behavior is required — any EOA can call `exactInputSingle`. The `DepositAllowlistExtension` correctly gates on `owner` (the position beneficiary) rather than `sender`, making this asymmetry easy to miss during review.

## Recommendation

Change `SwapAllowlistExtension.beforeSwap()` to gate on the `recipient` parameter (the address that economically benefits from the swap output) rather than `sender`:

```solidity
// Before (wrong actor — gates on intermediary router):
function beforeSwap(address sender, address, ...)
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])

// After (gate on recipient — the economic beneficiary):
function beforeSwap(address, address recipient, ...)
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient])
``` [3](#0-2) 

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension in beforeSwap slot.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    // allowlist the router so router-based swaps work
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: pool,
          recipient: attacker,
          zeroForOne: true,
          amountIn: X,
          ...
      })

Execution trace:
  1. Router calls pool.swap(recipient=attacker, ...)
     → pool's msg.sender = router
  2. Pool calls _beforeSwap(sender=router, recipient=attacker, ...)
  3. SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router] == true  ✓ (passes)
  4. Swap executes; attacker receives output tokens.

Result:
  - attacker successfully swaps on a curated pool despite never being allowlisted.
  - The allowlist extension is completely bypassed.
``` [1](#0-0) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
