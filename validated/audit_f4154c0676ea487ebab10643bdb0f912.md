### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` is called by the router, so `sender` resolves to the router address — not the actual user. This makes per-user allowlisting structurally incompatible with the router: if the router is allowlisted (necessary for allowlisted users to use the standard periphery), every user on the network can bypass the curated-pool restriction.

---

### Finding Description

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the `sender` argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly — the router is `msg.sender` of that call, so the extension sees `sender = router`:

```solidity
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
``` [4](#0-3) 

The original user's address is stored only in transient callback context (for token settlement), never forwarded to the pool or the extension. The extension has no way to recover it.

This creates an irreconcilable conflict for any pool that uses `SwapAllowlistExtension`:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use the router; they must call `pool.swap()` directly |
| Yes | Every user on the network can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks disallowed users.

---

### Impact Explanation

A disallowed user can execute swaps on a curated pool — one that the pool admin intended to restrict to specific counterparties (e.g., KYC'd institutions, whitelisted market makers) — simply by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The extension's guard fails open for all router-mediated swaps whenever the router address is allowlisted. LPs who deposited into the pool under the assumption that only vetted counterparties could trade against their liquidity are exposed to unrestricted swap flow, including adversarial or manipulative trades the allowlist was designed to prevent.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. A pool admin who wants allowlisted users to be able to use the router (the normal UX) must allowlist the router address. This is a natural and expected administrative action. Once the router is allowlisted, the bypass is available to any address with no further preconditions.

---

### Recommendation

The extension must check the original economic actor, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Router forwards user identity via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and verifies it. This requires a convention between the router and the extension.

2. **Pool exposes original sender via transient storage**: The pool stores the original `msg.sender` in transient storage before calling extensions, and extensions read it via a pool view. This keeps the interface clean but requires a core change.

Until fixed, pool admins using `SwapAllowlistExtension` must not allowlist the router address, accepting that allowlisted users cannot use the standard periphery.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  alice = allowlisted user
  bob   = disallowed user

Step 1 — admin allowlists alice and the router:
  swapExtension.setAllowedToSwap(pool, alice,   true)   // alice can swap directly
  swapExtension.setAllowedToSwap(pool, router,  true)   // so alice can also use the router

Step 2 — bob (not allowlisted) calls the router:
  router.exactInputSingle({pool: pool, recipient: bob, ...})

Step 3 — router calls pool.swap() with msg.sender = router:
  pool.swap(bob, zeroForOne, amount, priceLimit, "", extensionData)

Step 4 — pool calls _beforeSwap(msg.sender=router, ...):
  extension.beforeSwap(router, bob, ...)

Step 5 — extension evaluates:
  allowedSwapper[pool][router] == true  →  check passes

Step 6 — bob's swap executes successfully against LP liquidity,
          bypassing the per-user allowlist entirely.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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
