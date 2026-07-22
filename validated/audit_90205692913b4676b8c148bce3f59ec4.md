### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates on the `sender` parameter, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router (the natural action to enable router-based swaps), every non-allowlisted user can bypass the curation policy by routing through the router.

---

### Finding Description

**Call chain when a user swaps via `MetricOmmSimpleRouter.exactInputSingle()`:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `pool.swap(params.recipient, ...)` — `msg.sender` at the pool is the **router**.
3. Pool calls `_beforeSwap(msg.sender, recipient, ...)` = `_beforeSwap(router, user, ...)`.
4. `ExtensionCalling._callExtensionsInOrder` invokes `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. The extension evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`.

The check never touches the actual user's address.

**Relevant code:**

`MetricOmmPool.swap()` passes `msg.sender` (the router) as `sender` to the extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap()` gates on that `sender` parameter: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making itself `msg.sender` at the pool: [3](#0-2) 

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

- **If the router is allowlisted** (the natural action to enable router-based swaps): `allowedSwapper[pool][router] = true` passes for every call through the router, regardless of who the actual user is. Any non-allowlisted address can bypass the curation policy by calling `exactInputSingle` or any other router entry point.
- **If the router is not allowlisted**: every allowlisted user who tries to swap through the router is blocked, because the extension sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`.

In the first case the allowlist is completely defeated — a non-allowlisted user receives output tokens from a curated pool they should never have accessed. This is a direct loss of the pool's curation invariant and, depending on pool design, can result in unauthorized extraction of LP assets or protocol fees from a pool that was intended to be restricted.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented periphery swap path. Pool admins and users are expected to use it.
- A pool admin who wants router-based swaps to work at all must allowlist the router, which immediately opens the bypass to every address.
- No special privilege, flash loan, or unusual token behavior is required. Any EOA can call `exactInputSingle`.
- The `DepositAllowlistExtension` does **not** share this bug — it correctly gates on `owner` (the position beneficiary), not `sender`. The asymmetry makes the swap-side bug easy to miss.

---

### Recommendation

Change `SwapAllowlistExtension.beforeSwap()` to gate on the `recipient` parameter (the address that economically benefits from the swap output) rather than `sender` (the intermediary router). Alternatively, if the intent is to gate the payer/initiator, the pool must propagate the original `msg.sender` through the router via `extensionData` and the extension must decode it — but this requires a trusted encoding convention. The simplest correct fix is:

```solidity
// Before (wrong actor):
function beforeSwap(address sender, address, ...)
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])

// After (gate on recipient — the economic beneficiary):
function beforeSwap(address, address recipient, ...)
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient])
```

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension in beforeSwap slot.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router so router-based swaps work
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
``` [2](#0-1) [1](#0-0) [5](#0-4)

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
