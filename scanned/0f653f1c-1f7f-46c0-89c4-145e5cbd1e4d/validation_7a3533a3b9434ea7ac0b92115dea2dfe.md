### Title
`SwapAllowlistExtension` Checks Router Address Instead of End User — Allowlist Fully Bypassed When Router Is Allowlisted - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the actual end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the curated pool's swap allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol:231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes this value as the `sender` parameter and dispatches it to each extension in order: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value received from the pool — the router's address when the user entered through `MetricOmmSimpleRouter`:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router `msg.sender` of the pool call:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [4](#0-3) 

The allowlist therefore gates the **router contract address**, not the actual end user. A pool admin who allowlists the router (the natural action to permit router-mediated swaps for their curated users) simultaneously opens the pool to every user of that public router.

The same path exists for `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap` with `msg.sender = router`. [5](#0-4) 

---

### Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is intended to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Once the pool admin allowlists the router — a public, permissionless contract — the allowlist is rendered completely ineffective: any address can call `router.exactInputSingle` and swap against the pool. Non-allowlisted users gain full swap access, defeating the curation policy and potentially draining pool liquidity at oracle prices that were only intended for trusted counterparties.

---

### Likelihood Explanation

Pool admins who deploy a curated pool and want their allowlisted users to use the standard periphery router will naturally add the router to `allowedSwapper`. The protocol provides no warning that doing so opens the pool to all users. The bypass requires only a standard router call — no special privileges, no flash loans, no multi-step setup. Any user who discovers the allowlisted router can exploit it immediately.

---

### Recommendation

The extension must resolve the actual end user rather than the immediate pool caller. Two sound approaches:

1. **Pass the original `msg.sender` through the router.** Have the router encode the real user address in `extensionData` and have the extension decode it — but this requires trust in the router, which is fragile.

2. **Check `sender` against the allowlist only when `sender` is not a known periphery contract; otherwise check the user embedded in `extensionData`.** This is complex and error-prone.

3. **Preferred:** Document that `SwapAllowlistExtension` is incompatible with router-mediated flows and enforce this at the extension level by reverting if `msg.sender` (the pool's caller) is a known router, or by requiring direct pool calls only. Alternatively, redesign the hook interface so the pool passes both the immediate caller and the original transaction initiator (`tx.origin` is unsafe; a dedicated forwarding field is safer).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
  - Pool admin calls setAllowedToSwap(pool, router, true)      // to let alice use the router

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(...) with msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - check: allowedSwapper[pool][router] == true  → passes
  - bob's swap executes on the curated pool

Result:
  - bob bypasses the allowlist entirely
  - any user can repeat this for as long as the router is allowlisted
``` [3](#0-2) [1](#0-0) [6](#0-5)

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
