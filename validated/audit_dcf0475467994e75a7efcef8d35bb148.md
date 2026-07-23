### Title
`SwapAllowlistExtension` gates on the router address instead of the actual user, allowing any unprivileged user to bypass the swap allowlist via the router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on-chain can bypass the individual allowlist by calling through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

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

`msg.sender` here is the pool (correct), and `sender` is the first argument forwarded by the pool. The pool always passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly:

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

So `pool.swap()`'s `msg.sender` is the router, and the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The dilemma this creates:**

- If the pool admin does **not** allowlist the router: individually allowlisted users cannot use the router at all (usability broken).
- If the pool admin **does** allowlist the router (the only way to enable router usage): every user on-chain can bypass the individual allowlist by routing through the router.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

### Impact Explanation

Any user who is not on the allowlist can bypass the `SwapAllowlistExtension` guard entirely by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) against a pool that has the router allowlisted. The pool admin's intent to restrict swaps to specific addresses is completely defeated. Depending on the pool's purpose (e.g., KYC-gated, market-maker-only, or restricted-LP pools), this allows unauthorized parties to drain LP value through unrestricted swaps.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard periphery entry point for swaps. Any pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special setup. The router is a public, permissionless contract.

### Recommendation

The extension must check the **original transaction sender**, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original sender through `extensionData`**: The router encodes `msg.sender` into `extensionData`, and the extension decodes and verifies it. This requires the extension to trust the pool's forwarding of `extensionData`, which is already done faithfully.

2. **Check `tx.origin` as a fallback** (not recommended for general use, but acceptable for allowlist-only extensions where the pool admin controls the list).

3. **Preferred**: The pool should pass both `msg.sender` (direct caller) and an authenticated original-user field. Alternatively, the `SwapAllowlistExtension` should check `sender` only when `sender` is not a known router, and require routers to forward the real user identity via `extensionData`.

The simplest safe fix: the pool admin should never allowlist the router; instead, allowlisted users must call the pool directly. This should be documented as a hard constraint in the extension's NatSpec.

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps in a pool he was never authorized to access. [4](#0-3) [5](#0-4) [6](#0-5)

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
