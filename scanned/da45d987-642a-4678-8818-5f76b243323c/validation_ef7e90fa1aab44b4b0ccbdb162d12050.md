### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass the per-pool swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks whether the **immediate caller of `pool.swap()`** is allowlisted. When a user routes through `MetricOmmSimpleRouter`, the immediate caller is the router contract, not the user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for legitimate users), every unprivileged address can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` with itself as `msg.sender`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist status — not the originating user's. The pool admin faces an inescapable dilemma:

- **Do not allowlist the router** → every allowlisted user is blocked from using the router; they must call `pool.swap()` directly.
- **Allowlist the router** → every address on the network can bypass the allowlist by routing through the router.

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users.

The same structural problem applies to the multi-hop `exactInput` path (all hops call `pool.swap()` with `msg.sender = router`) and the recursive `exactOutput` path (the router calls each intermediate pool from within its own callback, so `msg.sender` is still the router at every hop): [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to be a permissioned venue — e.g., KYC-gated, institutional-only, or restricted to a specific set of counterparties. Any unprivileged address can bypass that restriction entirely by calling the public `MetricOmmSimpleRouter`. The allowlist extension provides no protection against router-mediated swaps once the router is allowlisted. This is an admin-boundary break: the pool admin's access-control configuration is bypassed by an unprivileged path through a public periphery contract.

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router (the normal UX) must allowlist the router, at which point the bypass is immediately available to every address. The trigger requires only a standard `exactInputSingle` call — no special privileges, no flash loans, no oracle manipulation.

---

### Recommendation

The extension must verify the **originating user**, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the original sender through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and verifies it. The pool admin allowlists individual users, not the router.

2. **Check `sender` against the allowlist and treat the router as transparent**: The router is never allowlisted; instead, the router is required to forward the real user address as `sender` (e.g., via a signed payload or a dedicated router-aware extension interface), and the extension checks that forwarded address.

Either way, the allowlist lookup must be keyed on the address that economically controls the swap, not the contract that mechanically calls `pool.swap()`.

---

### Proof of Concept

**Setup:**
- Deploy a pool with `SwapAllowlistExtension`.
- Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only permitted swapper.
- Pool admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.

**Attack:**
- Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
- Router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
- Pool calls `extension.beforeSwap(sender=router, ...)`.
- Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
- Bob's swap executes in a pool he was never permitted to access.

**Direct call (correctly blocked):**
- Bob calls `pool.swap(...)` directly.
- Pool calls `extension.beforeSwap(sender=bob, ...)`.
- Extension evaluates `allowedSwapper[pool][bob]` → `false` → reverts with `NotAllowedToSwap`.

The router path and the direct path produce opposite outcomes for the same unprivileged user, confirming the bypass. [3](#0-2) [1](#0-0) [7](#0-6)

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
