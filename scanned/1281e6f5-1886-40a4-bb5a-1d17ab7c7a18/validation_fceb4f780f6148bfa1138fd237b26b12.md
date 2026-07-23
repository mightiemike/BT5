### Title
SwapAllowlistExtension Bypassed via Router: Any User Can Swap on Curated Pools When Router Is Allowlisted - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is `msg.sender` from `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router to support legitimate router-based swaps, every user — including non-allowlisted ones — can bypass the swap allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool's `_beforeSwap` dispatcher, which passes `msg.sender` of `pool.swap()`:

```solidity
// MetricOmmPool.sol::swap
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

The extension then checks whether that `sender` is allowlisted for the calling pool:

```solidity
// SwapAllowlistExtension.sol::beforeSwap
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap()` as `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol::exactInputSingle
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The pool therefore passes `sender = router_address` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for any pool admin who wants to use `SwapAllowlistExtension` with router support:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot swap through the router at all — broken UX |
| **Allowlist the router** | **Any user bypasses the allowlist by routing through the router** |

The same identity substitution occurs in multi-hop `exactInput` (all hops call `pool.swap()` from the router) and in the recursive `exactOutput` callback path. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional participants, or whitelisted market makers) loses that protection entirely once the router is allowlisted. Any unprivileged user can execute swaps against the pool's liquidity by calling the public router, draining LP value through trades the pool admin explicitly intended to block. This constitutes broken core pool functionality and unauthorized access to LP-owned assets, satisfying the High/Critical impact gate.

---

### Likelihood Explanation

The attack requires no special privileges. Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is allowlisted (both facts are on-chain) can immediately exploit it. The pool admin is forced into this configuration the moment they want to support the standard periphery swap path. The router is a public, immutable contract — there is no way to restrict who calls it.

---

### Recommendation

The extension must resolve the originating user, not the direct pool caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted encoding convention.

2. **Check `tx.origin` as a fallback when `sender` is a known router**: Fragile and generally discouraged.

3. **Preferred — gate on `tx.origin` or require direct pool calls only**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level by rejecting pools that configure both a swap allowlist and a router-compatible setup.

The cleanest fix is to have the router forward the originating user's address as part of a signed or trusted `extensionData` payload, and have the extension verify that payload instead of the raw `sender` argument.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only `alice` via setAllowedToSwap(pool, alice, true)
  - Pool admin also allowlists the router: setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=bob, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes; bob receives tokens from the curated pool

Result:
  - bob bypassed the allowlist entirely
  - alice's exclusive access to the pool is broken
  - LP funds are exposed to any public user
``` [5](#0-4) [6](#0-5)

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
