### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. If the pool admin adds the router to the allowlist (the only way to enable router-based swaps on a curated pool), every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (enforced by `onlyPool` in `BaseMetricExtension`). `sender` is the first argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

So `msg.sender` inside `pool.swap()` is the **router address**, not the original user. The allowlist check becomes `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][user_address]`.

The same actor-binding mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of which call `pool.swap()` from the router contract. [4](#0-3) 

---

### Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. To allow those addresses to also use the router (the standard periphery path), the admin must add the router to the allowlist. The moment the router is allowlisted, **every address on the network** can call `router.exactInputSingle(pool, ...)` and the extension will see `sender = router_address`, which passes the check. The allowlist is completely defeated. Unauthorized users can drain liquidity from the curated pool at oracle prices, causing direct loss of LP principal.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public, permissionless contract — any address can call it.
- Pool admins who want their allowlisted users to use the router (the documented periphery path) must add the router to the allowlist, which is the natural operational step.
- No special privileges, no malicious setup, and no non-standard tokens are required. Any user who is not on the allowlist can bypass it in a single transaction.

---

### Recommendation

The extension must check the **economically relevant actor** — the original user — not the intermediary router. Two approaches:

1. **Pass the original payer/initiator through the extension data**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`, and the extension decodes and checks it. This requires a coordinated convention between the router and the extension.

2. **Check `recipient` or use a trusted-forwarder pattern**: The extension could require the router to attest the original sender in a verifiable way (e.g., a signed payload or a dedicated router field).

The simplest correct fix is for the router to encode the original `msg.sender` into `extensionData` and for `SwapAllowlistExtension.beforeSwap` to decode and check that value when `sender` is a known router address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)  → alice is allowlisted.
  - Pool admin calls setAllowedToSwap(pool, router, true) → router is allowlisted
    (required so alice can use the router).

Attack:
  - bob (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., recipient: bob})
  - Router calls pool.swap(...) with msg.sender = router.
  - SwapAllowlistExtension.beforeSwap receives sender = router_address.
  - Check: allowedSwapper[pool][router_address] == true → PASSES.
  - Bob's swap executes at oracle price, draining LP funds.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist fully bypassed.
``` [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
