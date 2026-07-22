### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper — Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes its own `msg.sender` as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual user. The extension therefore checks the router's address, not the real economic actor, producing the exact wrong-actor binding described in the external report.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol  (line ~231)
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end-user
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  (line ~160)
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  (line ~72)
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** the extension sees `sender = address(router)`, not the actual user. The allowlist is keyed on the wrong identity.

---

### Impact Explanation

Two fund-impacting outcomes arise:

**A — Full allowlist bypass (High).**  
A pool admin who wants to allow router-based swaps for a curated set of users must allowlist the router address. Once the router is allowlisted, *any* address — including addresses the admin explicitly excluded — can call `router.exactInputSingle()` and pass the `beforeSwap` guard. The curated pool's swap restriction is completely defeated; disallowed users trade freely.

**B — Allowlisted users locked out of the router (Medium).**  
If the admin allowlists only individual user addresses (not the router), those users cannot swap through the router at all, because the extension sees the router and reverts `NotAllowedToSwap`. This breaks the core swap flow for the intended participants.

Both outcomes are direct consequences of the wrong-actor binding: the guard checks the intermediary's address instead of the economic actor's address.

---

### Likelihood Explanation

`SwapAllowlistExtension` is a production periphery contract designed for curated pools. `MetricOmmSimpleRouter` is the primary supported swap entrypoint. Any pool that deploys both will encounter this mismatch on the first router-based swap. No special permissions, malicious setup, or non-standard tokens are required — a normal `exactInputSingle` call is sufficient.

---

### Recommendation

The pool must forward the **original initiator** rather than its own `msg.sender`. Two complementary fixes:

1. **Pool-side:** Add an optional `initiator` parameter to `swap()` (defaulting to `msg.sender`) so the router can pass the real user. Alternatively, store the initiator in transient storage before calling the extension, mirroring the existing `inSwap()` pattern.

2. **Extension-side (short-term):** `SwapAllowlistExtension.beforeSwap` should check the `sender` argument only when it is a known non-router address, or the pool should expose a transient `swapInitiator()` getter that the extension can read directly.

3. **Router-side:** `MetricOmmSimpleRouter` already stores the real `msg.sender` in `_setNextCallbackContext` for the callback. The same value should be made available to extensions, e.g., via a transient slot the extension can query.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // enable router path
  pool admin does NOT allowlist attacker

Attack:
  attacker calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls extension.beforeSwap(address(router), ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes — attacker bypasses the allowlist

Expected:
  extension should check allowedSwapper[pool][attacker] == false → revert NotAllowedToSwap
```

The attacker pays tokens and receives output tokens from a pool that was configured to exclude them, with no privileged access required beyond a standard router call.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
