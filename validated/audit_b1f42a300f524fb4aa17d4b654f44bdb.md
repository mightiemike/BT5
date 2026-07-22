### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool always sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool boundary is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the guard by calling the router instead of the pool directly.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` reads the first parameter (`sender`) and compares it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`MetricOmmPool.swap` always passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool boundary:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses. To allow those addresses to also use the standard periphery router, the admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the guard passes for **every** caller regardless of their individual allowlist status. Any unpermitted user can call `router.exactInputSingle(...)` targeting the curated pool and execute a swap that the allowlist was supposed to block. This is a complete bypass of the curation policy with direct fund-impacting consequences: disallowed users can drain or trade against LP positions in pools that were designed to be restricted.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the documented, standard periphery swap path. Pool admins who want allowlisted users to be able to use the router (the normal UX) must allowlist the router, which simultaneously opens the pool to all users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call the router. Likelihood is **High**.

---

### Recommendation

The `sender` argument passed to the extension must represent the economic actor (the end user), not the intermediary contract. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: pass the original `msg.sender` as an authenticated field inside `extensionData` and have the extension decode it. However, this is unauthenticated and spoofable by any caller.

2. **Preferred — in `MetricOmmPool.swap`**: add an explicit `address sender` parameter (separate from `msg.sender`) that the pool passes to the extension, and have the router forward `msg.sender` in that field. The pool can enforce that `msg.sender` is a factory-registered router before trusting the forwarded sender, or the extension can fall back to `msg.sender` when the caller is not a known router.

3. **Alternatively**: the extension should check `msg.sender` (the pool's caller) only when it is not a known router, and check a decoded user address from `extensionData` when it is — with the router being responsible for injecting and signing the real user identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to allow router-mediated swaps
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is not permitted

Attack:
  - alice calls router.exactInputSingle({pool: curatedPool, ...})
  - router calls pool.swap(recipient, ...)  →  msg.sender = router
  - pool calls _beforeSwap(router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  →  passes
  - alice's swap executes against the curated pool

Result:
  - alice, who is not on the allowlist, successfully swaps in a pool
    that was designed to restrict trading to permitted addresses only.
  - The allowlist guard is completely bypassed for all router-mediated swaps.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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
