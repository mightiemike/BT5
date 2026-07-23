### Title
`SwapAllowlistExtension` gates on the router's address instead of the actual swapper's identity, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. If the pool admin allowlists the router address to enable router-mediated swaps for their curated users, every unpermissioned user can bypass the allowlist by routing through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the identity check as:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the caller of the extension) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the `pool.swap()` call itself. [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

The router forwards no information about the original `msg.sender` (the real user) to the pool or to the extension. The `extensionData` field is passed through unchanged but `SwapAllowlistExtension` ignores it entirely.

**The invariant break:** A pool admin who wants their allowlisted users to be able to use the router must add the router address to `allowedSwapper[pool]`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every call that arrives through the router — regardless of who the actual user is. Any address that is not individually allowlisted can bypass the gate by calling `MetricOmmSimpleRouter.exactInputSingle` (or any of the other `exact*` entry points) instead of calling `pool.swap()` directly.

The same issue applies to multi-hop `exactInput` and `exactOutput` paths, where the router also calls `pool.swap()` as `msg.sender`. [4](#0-3) 

---

### Impact Explanation

A curated pool that relies on `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned bots, or whitelisted market makers) loses that protection entirely for any user who routes through the public `MetricOmmSimpleRouter`. The bypassing user can execute arbitrary swaps against the pool's liquidity at oracle-derived prices, extracting value that the pool admin intended to reserve for allowlisted participants. This is a direct loss of LP assets and a broken core pool functionality.

---

### Likelihood Explanation

The bypass requires the pool admin to have added the router to the allowlist. This is a natural operational step: a pool admin who deploys a curated pool and also wants their allowlisted users to benefit from the router's slippage protection and multi-hop routing will add the router to `allowedSwapper`. The admin has no on-chain mechanism to distinguish "router calling on behalf of an allowed user" from "router calling on behalf of a disallowed user," so the only practical option is to allowlist the router as a whole. Once that is done, the bypass is unconditional and requires no special privileges from the attacker.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the economically relevant actor — the end user — not the immediate caller of `pool.swap()`. Two concrete approaches:

1. **Forward the original user via `extensionData`:** The router encodes `msg.sender` into the `extensionData` it passes to the pool, and the extension decodes and verifies it (with a signature or a trusted-router flag). This requires a coordinated change to the router and the extension.

2. **Check `sender` against a router-aware allowlist:** Introduce a separate mapping for trusted routers and, when `sender` is a trusted router, require the extension to receive the real user identity through `extensionData` and verify it against `allowedSwapper`.

The simplest safe default is to not allowlist the router at all and require allowlisted users to call `pool.swap()` directly, but this removes router functionality for curated pools.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is meant to trade.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `_beforeSwap` dispatches to `SwapAllowlistExtension.beforeSwap` with `sender = router`.
7. The check `allowedSwapper[pool][router]` is `true` → no revert.
8. Bob's swap executes at oracle prices against the pool's LP assets, bypassing the intended allowlist. [1](#0-0) [5](#0-4) [6](#0-5) [7](#0-6)

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
