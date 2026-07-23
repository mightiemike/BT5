### Title
`SwapAllowlistExtension` checks router address instead of actual swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which equals `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the actual user's address. A pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently opens the pool to **all** users, fully bypassing the per-user allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

The pool's `swap()` function passes its own `msg.sender` as `sender` to `_beforeSwap`. When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

the pool sees `msg.sender = router`. It therefore passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`, **not** `allowedSwapper[pool][actual_user]`.

The same substitution occurs in every router entry point — `exactInput` (all hops), `exactOutputSingle`, `exactOutput`, and the recursive `_exactOutputIterateCallback` — because in every case the router is the direct caller of `pool.swap()`. [4](#0-3) 

This creates an irreconcilable dilemma for any pool admin who deploys this extension:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | **Blocked** (broken UX) | Blocked |
| Yes | Allowed | **Allowed** (bypass) |

There is no configuration that simultaneously permits allowlisted users to use the router while blocking non-allowlisted users.

---

### Impact Explanation

Any non-allowlisted user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. Once the router is allowlisted (the natural step a pool admin takes to support router-mediated swaps for their curated users), the allowlist provides zero protection against router-mediated swaps. The attacker can trade on pools restricted to specific counterparties (e.g., KYC/AML-gated institutional pools), receiving oracle-priced output tokens under the victim pool's compliance profile. This is a direct loss-of-policy-control impact on pools whose economic value depends on participant restriction.

---

### Likelihood Explanation

Medium. The trigger requires the pool admin to allowlist the router — a natural and expected configuration step when the admin wants their allowlisted users to be able to use the standard periphery. The admin has no way to achieve both goals (router support + per-user gating) with the current extension design, so the bypass is reachable on any pool that attempts to support router-mediated swaps.

---

### Recommendation

The extension must check the **economically responsible actor**, not the intermediary. Two viable approaches:

1. **Router-forwarded origin**: Have `MetricOmmSimpleRouter` pass `msg.sender` (the real user) as part of `extensionData` and have the extension decode and check it. This requires a convention between the router and the extension.
2. **Transient-storage payer**: The router already stores the real payer in transient storage (`_getPayer()`). The extension could read a standardized transient slot set by the router to identify the originating user.

Either way, `SwapAllowlistExtension.beforeSwap` must gate on the address that economically initiates the swap, not on the address that calls `pool.swap()`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — allowlisting a specific user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the router so `userA` can use it.
4. Non-allowlisted `userB` calls `router.exactInputSingle({pool: pool, recipient: userB, ...})`.
5. Router calls `pool.swap(userB, ...)` — pool sees `msg.sender = router`.
6. Pool calls `_beforeSwap(router, userB, ...)` → extension checks `allowedSwapper[pool][router]` → **true** → swap proceeds.
7. `userB` successfully swaps on the restricted pool despite never being allowlisted. [5](#0-4) [6](#0-5) [7](#0-6)

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
