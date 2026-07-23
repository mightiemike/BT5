### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Per-User Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which the pool sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the **router address**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension` is designed to gate swap access on a per-user basis for curated pools. Its `beforeSwap` hook receives `sender` as the first argument and checks:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the pool calls the extension), and `sender` is the first argument the pool passes — which is `msg.sender` of the pool's own `swap()` call. [1](#0-0) 

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender` to the extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When a user swaps through `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`:

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

So `msg.sender` inside `pool.swap()` is the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin action | Result |
|---|---|
| Allowlist individual users (not the router) | Allowlisted users **cannot** swap through the router; they must call the pool directly |
| Allowlist the router address | **All users** can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously enforces per-user restrictions and allows router-mediated swaps. The `sender` parameter is the wrong actor for the intended guard.

This is structurally identical to the Telcoin H-2 bug: a wrong address is bound into a critical check — there, `_target` (proxy target) was passed where the lockup contract was needed; here, the router address is checked where the end user address must be checked.

---

### Impact Explanation

A pool admin deploys a curated pool (e.g., KYC-gated, institutional-only) with `SwapAllowlistExtension` and allowlists specific user addresses. To also support the standard router interface, the admin must allowlist the router. Once the router is allowlisted, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()` targeting the curated pool and bypass the allowlist entirely. The guard fails open for all router-mediated swaps, allowing unauthorized users to trade against LP capital that was deposited under the assumption of a restricted pool.

---

### Likelihood Explanation

The router is the primary supported swap interface for end users. Any user can call it permissionlessly. The pool admin has no way to prevent this without removing the router from the allowlist entirely (which breaks router support for all users). The bypass requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must check the **end user identity**, not the immediate caller of the pool. Two viable approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool, and the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Separate `sender` from `initiator` in the hook interface**: The pool could pass both `msg.sender` (the direct caller) and an additional `initiator` field (populated by the router via a trusted mechanism) to the extension.

The `DepositAllowlistExtension` does not share this flaw because it correctly gates by `owner` (the position owner), which is independent of who calls the pool. [4](#0-3) 

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists alice (a KYC'd user) and the router (to support router swaps)
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT allowlisted
// bob calls the router directly — the extension sees sender = router (allowlisted) → passes
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// bob successfully swaps on a pool he was never allowlisted for
```

The extension receives `sender = address(router)`, which is allowlisted, so the guard passes for bob even though `allowedSwapper[pool][bob]` is `false`. [5](#0-4) [6](#0-5) [7](#0-6)

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
