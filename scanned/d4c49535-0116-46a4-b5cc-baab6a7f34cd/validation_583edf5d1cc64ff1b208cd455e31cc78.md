### Title
`SwapAllowlistExtension.beforeSwap()` checks the router's address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument forwarded by the pool, which equals `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router's address becomes `sender`. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the individual swap allowlist by calling through the router.

---

### Finding Description

In `SwapAllowlistExtension.beforeSwap()`, the guard checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the direct caller of `pool.swap()`. In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender` to the extension:

```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with `msg.sender = router`. The pool therefore passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. [3](#0-2) 

This is structurally inconsistent with `DepositAllowlistExtension`, which correctly checks `owner` (the actual position owner) rather than `sender` (the direct caller). The deposit allowlist works correctly through `MetricOmmPoolLiquidityAdder` because the adder passes the actual user's address as `owner`. The swap path has no equivalent mechanism — there is no "swap owner" parameter, and `extensionData` is not used by `SwapAllowlistExtension`. [4](#0-3) 

The result is a two-sided broken invariant:

1. **Bypass path**: If the pool admin allowlists the router (to enable router-mediated swaps), every unprivileged user can bypass the individual allowlist by calling through the router.
2. **Broken functionality path**: If the router is not allowlisted, individually allowlisted users cannot use the router at all.

There is no configuration that simultaneously restricts swaps to specific users AND allows those users to use the router.

---

### Impact Explanation

If the pool admin allowlists the router — a reasonable action to enable router-mediated swaps — the `SwapAllowlistExtension` guard is completely ineffective for router-mediated swaps. Any unprivileged user can swap in a pool intended to be restricted, potentially draining LP assets from a pool whose access control was supposed to gate specific counterparties. The allowlist guard, which is the only mechanism to restrict swap access, is silently nullified.

---

### Likelihood Explanation

The pool admin must configure `SwapAllowlistExtension` and allowlist the router. This is a plausible production configuration: an admin restricts swaps to specific users but also wants those users to be able to use the standard router. The admin allowlists both the individual users and the router, not realizing that allowlisting the router opens the pool to all users. The `MetricOmmSimpleRouter` is a public, permissionless contract callable by anyone.

---

### Recommendation

The `SwapAllowlistExtension` should gate on the actual end-user identity, not the direct caller. One approach: require the actual user's address to be passed in `extensionData` and verify it in the extension (with the router forwarding `msg.sender` in `extensionData`). Alternatively, document explicitly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and warn pool admins against doing so when individual-user gating is intended.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the intended allowlisted user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to enable router-mediated swaps for Alice.
4. Bob (unprivileged) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true`.
8. Bob's swap succeeds despite never being individually allowlisted. [5](#0-4) [6](#0-5) [7](#0-6)

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
