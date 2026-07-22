### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of the pool's `swap()` is the router, not the user. A pool admin who allowlists the router address (the only way to permit EOA users to reach the pool, since the pool's swap callback forces all callers to be contracts) inadvertently opens the pool to every user, bypassing the intended per-address gate.

---

### Finding Description

**Actor binding in the extension**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

**What the pool passes as `sender`**

`MetricOmmPool.swap` passes `msg.sender` (the immediate caller of the pool) as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged into the ABI-encoded call to the extension: [3](#0-2) 

**Why EOA users must go through the router**

`MetricOmmPool.swap` unconditionally calls `metricOmmSwapCallback` on `msg.sender` after executing the swap:

```solidity
IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
``` [4](#0-3) 

An EOA has no code, so this call reverts. Every EOA user must therefore route through `MetricOmmSimpleRouter` (or another callback-capable contract). When they do, `msg.sender` of `pool.swap()` is the router, not the user.

**The router passes itself as the effective sender**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly; the pool sees `msg.sender = router`:

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
``` [5](#0-4) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [6](#0-5) 

**The impossible configuration**

The pool admin faces a forced choice:

| Admin configuration | Allowlisted EOA users | Non-allowlisted EOA users |
|---|---|---|
| Allowlist individual EOA addresses only | **Blocked** (router not allowlisted) | Blocked |
| Allowlist the router | Allowed | **Also allowed — bypass** |

There is no configuration that simultaneously permits allowlisted EOA users to swap through the router and blocks non-allowlisted EOA users from doing the same. The extension checks `allowedSwapper[pool][router]`, a single boolean that is either true for everyone or false for everyone who uses the router.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address (the only practical way to let EOA users trade) grants every user on the network the ability to swap on that pool. Non-allowlisted users can drain LP positions by executing swaps that the allowlist was specifically configured to prevent. This is a direct loss of LP principal.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing entry point documented and deployed by the protocol. Any pool admin who wants their allowlisted users to be able to use the standard router must allowlist the router address. The mistake is structurally forced by the extension's actor-binding design, not by an unusual admin error. The trigger is an unprivileged EOA calling the public router after the admin has made this natural configuration choice.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic initiator** of the swap, not the immediate `msg.sender` of `pool.swap()`. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the user. This is imprecise for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router-aware extension**: Deploy a variant of `SwapAllowlistExtension` that, when `sender` is a known trusted router, reads the original initiator

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

**File:** metric-core/contracts/MetricOmmPool.sol (L258-263)
```text
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
