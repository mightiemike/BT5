Looking at the `SwapAllowlistExtension` and how `MetricOmmSimpleRouter` interacts with it, I can identify a concrete analog to the external bug's "wrong actor binding" class.

---

### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the per-pool swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. That argument is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router address**, not the actual user. If the router is allowlisted (which is required for any router-mediated swap to succeed), every non-allowlisted user can bypass the curated pool's swap gate by simply calling the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

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
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever `msg.sender` was when `pool.swap()` was called. [1](#0-0) 

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to the extension dispatcher:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly — the original user's address is stored only in transient storage for the payment callback and is **never forwarded to the pool**:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
``` [3](#0-2) 

The pool therefore sees `msg.sender = router`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The same identity substitution occurs for multi-hop `exactInput` (all hops after the first use `address(this)` as payer, and the pool always sees `msg.sender = router`) and `exactOutput`. [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. To allow those allowlisted users to use the standard router interface, the admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **every user** — including those explicitly excluded — can bypass the gate by calling `router.exactInputSingle()`. The extension will see `sender = router` (allowlisted) and permit the swap.

The pool admin faces an impossible choice: either allowlist the router (breaking the allowlist for everyone) or do not allowlist the router (making the router unusable for legitimate users too). There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

Impact: unauthorized users can trade against LP positions in a pool that was designed to restrict counterparties, directly exposing LP principal to unintended swap flows.

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the standard `MetricOmmSimpleRouter` is affected. The router is the primary supported periphery path. A pool admin who allowlists the router to enable normal UX inadvertently opens the gate to all users. The attacker needs no special privileges — a single `exactInputSingle` call suffices.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economically relevant actor** (the end user), not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension verifies it (requires a trust assumption on the router, or a signature scheme).
2. **Check `sender` only for direct pool calls; require a separate identity proof for router calls**: The extension can detect router-mediated calls and apply a different check.
3. **Simplest fix**: Document that `SwapAllowlistExtension` is incompatible with the router and must only be used with direct pool calls, and add a guard in the extension that reverts if `sender` is a known router address.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin allowlists alice:  allowedSwapper[pool][alice] = true
3. Pool admin allowlists router: allowedSwapper[pool][router] = true
   (required so alice can use the standard router interface)
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender to pool = router.
6. Pool calls extension.beforeSwap(router, ...) — msg.sender to extension = pool.
7. Extension checks: allowedSwapper[pool][router] == true → passes.
8. bob's swap executes successfully, bypassing the allowlist.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
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
