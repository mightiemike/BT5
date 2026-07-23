### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — Router Address Replaces End-User Identity in `beforeSwap` Hook - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted — not the actual end user. If the pool admin allowlists the router to support legitimate router-based swaps, every user on the internet can bypass the individual-user allowlist by calling any of the router's `exact*` functions.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the caller of the extension) and `sender` is the first argument forwarded from the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // whoever called pool.swap() — the router when routing
    recipient,
    ...
)
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` with itself as `msg.sender`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The pool therefore passes the **router's address** as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual end user's identity is permanently lost at the pool boundary.

This creates an irreconcilable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

- **If the router is NOT allowlisted:** legitimate allowlisted users cannot use the router at all — every router-mediated swap reverts `NotAllowedToSwap`.
- **If the router IS allowlisted** (the only way to support router UX): the allowlist is completely bypassed — any address on the internet can call `router.exactInputSingle` and trade on the curated pool.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` because all of them call `pool.swap()` with `msg.sender = router`. [4](#0-3) 

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to KYC-verified counterparties, institutional market makers, or whitelisted addresses cannot enforce that restriction when the router is involved. Any unprivileged user can:

1. Call `router.exactInputSingle` targeting the curated pool.
2. The extension sees `sender = router` (allowlisted), passes the check.
3. The unauthorized user executes a swap, receiving output tokens from LP reserves.

LP funds are directly at risk: unauthorized traders can extract value from pools that were designed to only interact with trusted counterparties. This is a broken core pool functionality / admin-boundary break with direct loss of LP assets.

---

### Likelihood Explanation

Any pool that uses `SwapAllowlistExtension` and wants to support the standard periphery UX (router-based swaps) must allowlist the router. The router is a public, permissionless contract. Once the router is allowlisted, the bypass is trivially reachable by any address with no preconditions, no capital requirement beyond the swap input, and no special knowledge. The attack requires a single public transaction.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **actual end user**, not the intermediary. Two approaches:

1. **Pass the original caller through the router.** The router could forward the original `msg.sender` in `extensionData`, and the extension could decode and check it. This requires a trusted router (the extension must verify the caller is a known router before trusting the forwarded identity).

2. **Check `tx.origin` as a fallback.** When `msg.sender` (the `sender` argument) is a known router, fall back to `tx.origin`. This is fragile and generally discouraged but closes the immediate bypass.

3. **Restrict the allowlist to direct pool calls only** and document that router-mediated swaps are not subject to the allowlist — but this defeats the purpose of the extension for curated pools.

The cleanest fix is option 1: the router should forward the original caller in `extensionData`, and the extension should verify the router's identity before trusting the forwarded address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin allowlists alice (a legitimate user) and the router (to support router UX)
  - Pool admin does NOT allowlist bob (an unauthorized user)

Attack:
  1. bob calls router.exactInputSingle({pool: curated_pool, ...})
  2. router calls pool.swap(recipient=bob, ...)
  3. pool calls extension.beforeSwap(sender=router, ...)
  4. extension checks: allowedSwapper[pool][router] == true → passes
  5. swap executes; bob receives output tokens from LP reserves

Result:
  - bob, who was never allowlisted, successfully swaps on the curated pool
  - The SwapAllowlistExtension guard is silently bypassed
  - LP funds flow to an unauthorized counterparty
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
