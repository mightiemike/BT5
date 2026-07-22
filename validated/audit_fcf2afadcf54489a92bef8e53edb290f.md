### Title
`SwapAllowlistExtension` is fully bypassed when the router is allowlisted, letting any unprivileged user swap against a restricted pool — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router**, not the original user. If the pool admin allowlists the router (the only way to let allowlisted users trade via the router), the check degenerates to a single shared identity for all router users, and any unprivileged address can bypass the per-user gate by routing through the public router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the `pool.swap()` call:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` faithfully forwards this value:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` as `msg.sender`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

So the extension receives `sender = address(router)`, not the original user. The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router (the only way to let allowlisted users trade via the router), the gate collapses to a single shared identity: any user who calls the router passes the check, regardless of whether they are individually allowlisted.

The same structural problem applies to multi-hop `exactInput` and `exactOutput` paths. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a private institutional pool, a KYC-gated pool, or a pool with a limited counterparty set) can be freely traded against by any unprivileged address simply by routing through `MetricOmmSimpleRouter`. The LP positions in the pool are exposed to trades from actors the pool admin explicitly intended to exclude. Because the pool is oracle-anchored, an excluded actor can execute swaps at the oracle mid-price, extracting value from LPs or front-running oracle updates in ways the allowlist was designed to prevent.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard public entry point for swaps. Any user who observes that a pool has a swap allowlist and that the router is allowlisted (or who simply tries the router and finds it works) can exploit this. No special privileges, flash loans, or contract deployment are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must check the **original user**, not the immediate caller of `pool.swap`. Two approaches:

1. **Pass the original user through the router**: The router should encode the original `msg.sender` in `callbackData` or `extensionData` and the extension should decode and verify it. This requires a trusted forwarding convention.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the original user, but this is not reliable for multi-hop paths.

3. **Preferred — require direct pool interaction for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router address, and that allowlisted users must call `pool.swap` directly. Add a NatSpec warning to `SwapAllowlistExtension` and `MetricOmmSimpleRouter` to this effect.

The cleanest fix is to redesign the extension to accept a signed proof or to have the router forward the original caller's address in `extensionData`, which the extension then verifies.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only `alice` should be able to swap.
3. Pool admin also calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — necessary so `alice` can use the router.
4. `charlie` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: charlie, ...})`.
5. Router calls `pool.swap(charlie, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, charlie, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. Swap executes successfully for `charlie`, bypassing the per-user allowlist entirely.

The allowlist invariant — "only `alice` may swap against this pool" — is broken. `charlie` receives pool output tokens and the LP bears the trade. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
