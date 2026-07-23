### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Restrictions — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's. A pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to every user, completely defeating the per-user allowlist.

---

### Finding Description

**Root cause — wrong actor bound in the hook**

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call.

**How the pool populates `sender`**

`MetricOmmPool.swap` passes `msg.sender` directly:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [3](#0-2) 

**How the router calls the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

The router is `msg.sender` of `pool.swap()`, so `sender` arriving at the extension is the **router address**, not the end user.

**The bypass**

| Scenario | `allowedSwapper[pool][router]` | Result |
|---|---|---|
| Admin allowlists specific users, not the router | `false` | Allowlisted users cannot use the router at all (broken functionality) |
| Admin allowlists the router to support router-based swaps | `true` | **Every user** can swap through the router, bypassing the per-user restriction |

The second scenario is the natural production configuration: a pool admin who wants allowlisted users to be able to use the public router must allowlist the router address. Doing so silently grants swap access to all users, including those explicitly not on the allowlist.

The same path exists for multi-hop `exactInput` and `exactOutput` — every hop calls `pool.swap()` with the router as `msg.sender`: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional traders, or whitelisted market-makers) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the restricted pool. If the pool offers tighter spreads or special pricing intended only for allowlisted counterparties, unauthorized traders can exploit that pricing, extracting value from LPs. This is a direct admin-boundary break: the pool admin's configured allowlist policy is bypassed by a public, unprivileged periphery path.

---

### Likelihood Explanation

The likelihood is **medium-high**. Any production pool that:
1. Deploys with `SwapAllowlistExtension` to restrict swappers, **and**
2. Allowlists the router so that legitimate users can trade via the standard periphery

is fully exposed. This is the expected operational pattern — requiring users to call the pool directly (bypassing the router) is not a realistic mitigation for a live protocol.

---

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two sound approaches:

1. **Router-forwarded identity in `extensionData`**: Require the router to encode the original `msg.sender` into `extensionData` for each hop, and have the extension decode and verify that identity. The pool's `extensionData` parameter is already forwarded unchanged from the router to the extension.

2. **Separate allowlist entry for router-mediated swaps**: Extend the extension to accept a `(pool, router, user)` triple, where the router is expected to encode the user in `extensionData`, and the extension verifies both that the router is trusted and that the encoded user is allowlisted.

Avoid `tx.origin` — it breaks contract-to-contract composability and introduces phishing vectors.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as `extension1`, wired into `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is supposed to trade.
3. Admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use the router.
4. Charlie (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Charlie successfully trades on a pool he was never supposed to access, with no revert. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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
