### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Per-User Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end-user. If the pool admin allowlists the router (required for any user to swap through it), every unpermissioned user can bypass the per-user restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the first parameter forwarded by the pool. The pool always passes its own `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly:

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

So `pool.swap()`'s `msg.sender` is the **router**, not the end-user. The extension therefore checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

The same mismatch exists in `exactOutputSingle`, `exactInput`, and `exactOutput`, and also in the recursive `_exactOutputIterateCallback` path where intermediate hops call `pool.swap(msg.sender=router, ...)`. [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the actual position beneficiary), not the `sender` (the caller of `pool.addLiquidity()`):

```solidity
// DepositAllowlistExtension.sol line 38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [5](#0-4) 

The swap extension has no equivalent "who is the real beneficiary" parameter to check — the `recipient` is the output receiver, not the economic actor initiating the trade.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool admin is forced into a binary choice:

1. **Do not allowlist the router** → no user can swap through the router (breaks standard periphery usage).
2. **Allowlist the router** → every user, including those explicitly not in the allowlist, can swap through the router.

There is no configuration that allows selective per-user enforcement through the router. Any non-allowlisted user can execute swaps against a "curated" pool, receiving output tokens at oracle-anchored prices. This is a direct bypass of a core access-control invariant with fund-impacting consequences (unauthorized parties drain pool liquidity at oracle prices).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery swap path. Pool admins who deploy `SwapAllowlistExtension` to restrict access will naturally also allowlist the router so that their permitted users can trade conveniently. The moment the router is allowlisted, the bypass is open to everyone. No special privileges, flash loans, or unusual token behavior are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The `beforeSwap` hook must gate on the actual end-user identity, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the real user via `extensionData`**: Require callers (including the router) to encode the originating user address in `extensionData`, and verify it in the extension. The router would need to be updated to forward `msg.sender` in `extensionData`.

2. **Check `recipient` as a proxy**: For single-hop swaps where the user is also the recipient, checking `recipient` instead of `sender` would be more accurate. However, this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Redesign the hook signature**: Add an explicit `originator` field to `beforeSwap` that the pool populates from a trusted source (e.g., a signed permit or transient context set by the router), analogous to how `DepositAllowlistExtension` uses `owner` rather than `sender`.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is permitted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
  - bob is NOT in the allowlist

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ..., recipient: bob})
  2. Router calls pool.swap(bob, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes; bob receives output tokens
  6. SwapAllowlistExtension never checked bob's address
``` [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
