### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, any unprivileged address can bypass the allowlist entirely by routing through the router.

---

### Finding Description

**Actor binding mismatch in `SwapAllowlistExtension`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

The `sender` value originates from `MetricOmmPool.swap`, which passes `msg.sender` of the pool call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← pool's msg.sender, not the end user
  recipient,
  ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the `sender` argument to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` — so the pool's `msg.sender` is the **router address**, not the end user:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,   // recipient
  ...
);
``` [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

**Result:** The extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. This creates two mutually exclusive broken states:

| Admin configuration | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Allowlist specific users (not router) | **BLOCKED** (broken functionality) | Blocked (correct, wrong reason) |
| Allowlist the router address | Passes | **PASSES** (allowlist bypassed) |

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the LP provides liquidity only to trusted counterparties, typically at tighter spreads or with specific risk assumptions. If the pool admin allowlists the router address (the natural step to allow allowlisted users to use the supported periphery), any unprivileged address can call `router.exactInputSingle()` and trade against the pool. The LP suffers adverse selection losses from counterparties they explicitly excluded. This is a direct loss of LP principal/fees above Sherlock thresholds, matching the "allowlist bypass" impact gate.

---

### Likelihood Explanation

The router is the primary supported swap periphery. Pool admins who configure a swap allowlist and want their allowlisted users to use the router will naturally allowlist the router address — this is the only way to make router-mediated swaps work for legitimate users. The bypass is then unconditional: any address calling the router reaches the allowlisted pool. No special privileges, no malicious setup, no non-standard tokens required.

---

### Recommendation

The extension must check the **economically relevant actor** — the end user — not the intermediary. The pool already passes both `sender` (pool's `msg.sender`) and `recipient` to the hook. However, neither is the end user when routing through the router.

The correct fix is to have the router forward the actual user identity in `extensionData`, and have the extension decode it. Alternatively, the pool's `swap` interface should accept an explicit `swapper` parameter distinct from `msg.sender`, so the router can attest the real user. Until then, the allowlist should document that it only gates direct `pool.swap()` calls and cannot be used with the router.

A minimal mitigation: the extension should revert if `sender` is a known router/intermediary and no explicit user identity is provided in `extensionData`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can swap via router)
  - Pool admin calls setAllowedToSwap(pool, alice, true)
    (alice is the intended allowlisted user)
  - bob is NOT allowlisted

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — pool's msg.sender = router
  3. Pool calls _beforeSwap(router, recipient, ...)
  4. ExtensionCalling encodes sender=router and calls SwapAllowlistExtension.beforeSwap
  5. Extension checks allowedSwapper[pool][router] == true → PASSES
  6. bob's swap executes against the curated pool

Result:
  bob, who is not in the allowlist, successfully swaps against the pool.
  The allowlist is completely bypassed for all router-mediated swaps.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```
