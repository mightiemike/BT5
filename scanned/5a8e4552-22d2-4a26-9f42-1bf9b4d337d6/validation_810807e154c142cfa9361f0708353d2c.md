### Title
`SwapAllowlistExtension` checks router address instead of actual user — allowlist bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's address — not the actual end user. A pool admin who allowlists the router to support normal periphery usage inadvertently opens the gate to every user, defeating the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool from its own `swap()` call — which is the pool's `msg.sender`, i.e. whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no explicit sender argument:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [2](#0-1) 

The pool therefore sees `msg.sender = router` and passes `sender = router` to `ExtensionCalling._beforeSwap`: [3](#0-2) 

The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same pattern applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops): [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants to support the standard periphery router must allowlist the router address. Once the router is allowlisted, **every user** — including those the admin explicitly excluded — can call `router.exactInputSingle` (or any other router entry point) and the extension will see `sender = router`, pass the check, and execute the swap. The allowlist is completely bypassed. Unauthorized users can drain liquidity from a pool that was designed to be restricted, causing direct loss of LP assets and breaking the core curation invariant.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point. Any pool admin who wants users to interact normally through the router must allowlist it. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — any user who knows the pool address and the router address can exploit it immediately. Likelihood is high.

---

### Recommendation

The pool must pass the **original caller's address** to the extension, not the intermediary's address. Two complementary fixes:

1. **In the pool's `swap()` function:** accept an explicit `sender` parameter from the caller (analogous to how Uniswap v4 passes `msg.sender` through the unlock/action path), or expose a separate `swapOnBehalf(address sender, ...)` entry point that the router can call with the real user address.

2. **In `SwapAllowlistExtension`:** if the pool cannot be changed, gate on `recipient` or require the router to forward the real user address in `extensionData` and decode it inside the hook — but this is fragile and should be a last resort.

The root fix is ensuring the identity the extension checks is the same identity the pool admin intended to gate.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: allowedSwapper[pool][alice] = true   // alice is the intended user
  - Pool admin: allowedSwapper[pool][router] = true  // required for router-mediated swaps

Attack:
  - mallory (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: mallory, ...})
  - Router calls pool.swap(...) → pool sees msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → PASSES
  - Mallory's swap executes on a pool she was not authorized to access

Result:
  - Allowlist is bypassed; mallory extracts value from a curated LP pool
``` [5](#0-4) [6](#0-5)

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
