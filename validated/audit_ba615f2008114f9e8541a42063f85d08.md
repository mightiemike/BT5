### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Swap Allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for their curated users, any unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument passed by the pool — which is `msg.sender` of `pool.swap()`. [1](#0-0) 

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // <-- immediate caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then forwards this value verbatim as the first argument to every configured extension: [3](#0-2) 

When a user swaps through `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
``` [4](#0-3) 

So `msg.sender` inside `pool.swap()` is the router contract, not the original user. The extension therefore receives `sender = router`, and checks `allowedSwapper[pool][router]`.

The pool admin has no way to enforce per-user allowlisting for router-mediated swaps. The only options are:

- **Do not allowlist the router** → allowlisted users cannot use the router at all (broken UX).
- **Allowlist the router** → all users, including non-allowlisted ones, can bypass the allowlist by routing through the router.

The router does not inject any verified user identity into `extensionData`; that field is user-controlled and passed through unmodified, so the extension cannot rely on it to recover the true caller. [5](#0-4) 

The same wrong-actor binding applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput` paths, where the router is always the direct caller of each `pool.swap()`. [6](#0-5) 

---

### Impact Explanation

A curated pool whose pool admin has configured `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., trusted market makers, KYC-verified addresses, or protocol-internal actors) can be accessed by any unprivileged user simply by routing through `MetricOmmSimpleRouter`. The allowlist — the sole on-chain enforcement mechanism for swap curation — is rendered ineffective for the router path. LPs who deposited under the assumption that only vetted counterparties would trade against them are exposed to adverse selection, front-running, or other trading patterns the allowlist was designed to prevent, resulting in direct loss of LP principal or fees.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected operational step: a pool admin who wants their allowlisted users to be able to use the standard periphery router will allowlist the router, not realizing that doing so opens the gate to all users. The router is a public, permissionless contract, so any user can call it. The pool admin has no way to distinguish between different users going through the router.

---

### Recommendation

The `SwapAllowlistExtension` should gate on the economically relevant actor — the original transaction initiator — rather than the immediate caller of `pool.swap()`. One approach: require the router to encode and sign the actual user's address in `extensionData`, and have the extension verify this signature. A simpler but less general approach: document clearly that allowlisting the router is equivalent to `allowAllSwappers = true`, and provide a separate router-aware extension that reads user identity from a trusted transient-storage slot set by the router before calling the pool.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as a configured extension.
2. Pool admin allowlists `userA`: `extension.setAllowedToSwap(pool, userA, true)`.
3. Pool admin allowlists the router to support router-mediated swaps: `extension.setAllowedToSwap(pool, router, true)`.
4. `userC` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → does not revert.
8. `userC` successfully executes a swap on the curated pool, bypassing the per-user allowlist entirely. [1](#0-0) [7](#0-6) [5](#0-4)

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
