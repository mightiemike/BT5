### Title
`SwapAllowlistExtension` gates the router address instead of the real user, allowing any caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the user. The extension therefore gates the router's address, not the actual economic actor. If the pool admin allowlists the router to support the standard periphery path, every unpermissioned user can bypass the curated allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes this value and calls each extension:

```solidity
// ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

The pool's `msg.sender` is now the **router**, so `sender = router` reaches the extension. The allowlist check becomes `allowedSwapper[pool][router]` — it never sees the real user's address.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → all router-mediated swaps revert, even for legitimately allowlisted users, breaking the standard periphery path.
- **Allowlist the router** → every user, including those explicitly excluded from the allowlist, can bypass the gate by routing through the router.

The same structural flaw applies to multi-hop `exactInput` and `exactOutput` paths, where intermediate hops also present `address(this)` (the router) as `sender`.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses loses that protection entirely for any user who routes through the supported `MetricOmmSimpleRouter`. An unpermissioned user can execute swaps on a pool that was explicitly designed to exclude them, draining LP value or executing trades the pool admin intended to block. This is a direct bypass of a core access-control mechanism with fund-impacting consequences.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps. Any user aware of the allowlist restriction can trivially route through the router. The pool admin has no way to simultaneously support the router and enforce per-user allowlisting with the current extension design. No privileged setup or special token behavior is required.

---

### Recommendation

The extension must receive and gate on the **original user's address**, not the intermediary router's address. Two complementary fixes:

1. **Thread the originating user through the pool**: Add an `originator` field to the swap call or use transient storage so the pool can pass the true initiating address to extensions alongside `sender`.
2. **Router-side forwarding**: Have the router encode the real `msg.sender` inside `extensionData` and have the allowlist extension decode and verify it — but this requires the extension to trust the router, which introduces its own trust assumptions.

The cleanest fix is option 1: the pool should distinguish between the direct caller (`msg.sender`, used for callback settlement) and the originating user (passed explicitly to extension hooks), mirroring how `MetricOmmPoolLiquidityAdder` already separates `sender` from `owner` on the liquidity path.

---

### Proof of Concept

**Setup:** Pool P is deployed with `SwapAllowlistExtension`. Admin allowlists `alice` and the router (to support periphery usage). `bob` is explicitly not allowlisted.

**Attack:**
1. `bob` calls `MetricOmmSimpleRouter.exactInputSingle({ pool: P, ... })`.
2. Router calls `P.swap(recipient, zeroForOne, amount, limit, "", extensionData)`.
3. Pool calls `_beforeSwap(msg.sender=router, ...)`.
4. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[P][router]` → `true` → passes.
5. `bob`'s swap executes on the curated pool despite being explicitly excluded from the allowlist.

**Expected:** revert `NotAllowedToSwap`.
**Actual:** swap succeeds; `bob` trades on a pool he was never permitted to access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
