### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any caller to bypass a curated pool's swap allowlist via the public router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the allowlist by calling the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever the pool passes as the first argument to `_beforeSwap`. In `ExtensionCalling._beforeSwap`, the pool forwards its own `msg.sender` — i.e., the direct caller of `MetricOmmPool.swap` — as `sender`:

```solidity
// ExtensionCalling.sol L149-177
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

At this point `msg.sender` inside the pool is the **router**, not the originating user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Exploit path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or whitelisted addresses.
2. To let those whitelisted users interact via the standard router, the admin calls `setAllowedToSwap(pool, router, true)`.
3. Any unprivileged user now calls `router.exactInputSingle(...)` targeting the curated pool. The extension sees `sender = router`, finds it allowlisted, and permits the swap — the individual user identity is never checked.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router, and to any intermediate hop in a multi-hop path where the router calls `pool.swap` from inside `_exactOutputIterateCallback` with `msg.sender` (the pool) as recipient.

---

### Impact Explanation

**High.** A curated pool's entire access-control policy is nullified. Any user — including those the admin explicitly excluded — can execute swaps against the pool by routing through the public `MetricOmmSimpleRouter`. This directly exposes LP assets to trades from actors the pool was designed to exclude, and can drain LP value through arbitrage or adversarial trading that the allowlist was meant to prevent.

---

### Likelihood Explanation

**High.** The router is the standard, documented entry point for swaps. A pool admin who wants allowlisted users to use the router must allowlist the router address, which is the natural and expected configuration. The bypass requires no special knowledge beyond knowing the router address and calling a standard function.

---

### Recommendation

The extension must recover the originating user rather than trusting the `sender` parameter, which reflects only the immediate pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: require callers (including the router) to encode the originating user in `extensionData`, and verify it matches a signed or trusted source. This requires router cooperation and is fragile.

2. **Check `tx.origin` as a fallback** (not recommended for general use, but acceptable in allowlist contexts where the goal is to gate EOAs).

3. **Preferred — router-aware forwarding**: modify `MetricOmmSimpleRouter` to encode the originating `msg.sender` in `extensionData`, and update `SwapAllowlistExtension` to decode and check that address when `sender` is a known router. The extension can maintain a registry of trusted routers and, when `sender` is a trusted router, extract the real user from `extensionData`.

4. **Simplest — do not allowlist the router**: document that allowlisted pools must be accessed directly via `pool.swap`, not via the router. This is a usability constraint but closes the bypass without code changes.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the intended gated user)
  - allowedSwapper[pool][router] = true  (admin enables router for alice)

Attack:
  - bob (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: bob, ...})
  - Router calls pool.swap(bob, ...) — msg.sender in pool = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → swap proceeds
  - Bob successfully swaps on a pool he was explicitly excluded from
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
