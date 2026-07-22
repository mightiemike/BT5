### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` (the immediate caller of `pool.swap()`). When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to support router-mediated swaps, every user — including non-allowlisted ones — can bypass the per-user gate by calling any `exact*` function on the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct for the pool-keyed mapping), and `sender` is the first argument forwarded by the pool. The pool always sets that argument to its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-L240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-L80
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

The pool's `msg.sender` is now the router, so the extension receives `sender = router_address`. The extension then evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][end_user]`.

This creates an irreconcilable dilemma for any pool admin who wants both a per-user allowlist and router support:

- **If the router is allowlisted**: `allowedSwapper[pool][router] = true` → every user who calls the router passes the check, defeating the allowlist entirely.
- **If the router is not allowlisted**: allowlisted users cannot use the router at all, breaking the supported periphery path.

The same actor-substitution applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, including the recursive callback path in `_exactOutputIterateCallback` where the router calls `pool.swap(msg.sender, ...)` with `msg.sender` still being the router.

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institutional-only) with `SwapAllowlistExtension` and allowlists the router to support standard UX loses the entire per-user curation guarantee. Any unprivileged user can call `router.exactInputSingle()` and execute swaps on the curated pool. The pool's token balances change, LP positions are affected by price movement, and the curation policy — the only mechanism protecting LP funds from unauthorized counterparties — is silently nullified.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user who discovers that the router is allowlisted (observable on-chain via `allowedSwapper[pool][router]`) can immediately exploit this. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices.

### Recommendation

The extension must resolve the end user's identity rather than the immediate pool caller. Two approaches:

1. **Pass the original user through the router**: Have the router forward the original `msg.sender` as a separate field in `extensionData`, and have the extension decode and check that address. This requires a convention between the router and the extension.

2. **Check `sender` against a router-aware allowlist**: The extension could maintain a separate mapping of trusted routers and, when `sender` is a trusted router, extract the real user from `extensionData` (which the router must populate with `abi.encode(msg.sender)`).

The simplest safe fix is option 2: the router encodes the original caller into `extensionData`, and the extension decodes it when `sender` is a known router.

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  pool admin calls E.setAllowedToSwap(P, router, true)   // to enable router UX
  pool admin does NOT call E.setAllowedToSwap(P, attacker, true)

Attack:
  attacker calls router.exactInputSingle({pool: P, ...})
    → router calls P.swap(recipient, ...)
    → P calls _beforeSwap(msg.sender=router, ...)
    → E.beforeSwap(sender=router, ...) is called
    → allowedSwapper[P][router] == true  → check passes
    → swap executes for attacker
    → attacker receives output tokens from curated pool
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L201-233)
```text
  function _exactOutputIterateCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) private {
    ExactOutputIterateCallbackData memory cb = abi.decode(data, (ExactOutputIterateCallbackData));

    int256 amountToPay = MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta);
    uint8 tradesLeft = _getTradesLeft();

    if (tradesLeft == 0) {
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 amountIn = uint256(amountToPay);
      if (amountIn > cb.amountInMax) revert InputTooHigh(amountIn, cb.amountInMax);
      _setExactOutputAmountIn(amountIn);
      pay(_getTokenToPay(), _getPayer(), msg.sender, amountIn);
      return;
    }
    tradesLeft--;
    address pool = cb.pools[tradesLeft];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(cb.zeroForOneBitMap, tradesLeft);
    _updateCallbackContextforRecursiveOutput(pool, tradesLeft);

    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0DeltaReturned, amount1DeltaReturned);

    if (amountOut != amountToPay) revert InvalidOutputAmountAtHop(tradesLeft, amountOut, amountToPay);
  }
```
