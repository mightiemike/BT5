### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps for their curated pool, every unpermissioned user can bypass the allowlist by calling through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol:231
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` encodes that value as the first argument to `IMetricOmmExtensions.beforeSwap`:

```solidity
// ExtensionCalling.sol:163-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that value against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the call chain is:

```
User → Router.exactInputSingle()
     → Pool.swap(recipient, ...) [msg.sender = Router]
     → Extension.beforeSwap(sender = Router, ...)
```

The extension therefore checks `allowedSwapper[pool][Router]`, not `allowedSwapper[pool][User]`.

A pool admin who wants to allow router-mediated swaps for their curated pool must allowlist the router address. Once `allowedSwapper[pool][Router] = true`, every user — including those the admin explicitly excluded — can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The admin has no way to simultaneously enable router access and enforce per-user gating.

The same structural flaw exists for multi-hop `exactInput` (intermediate hops use `address(this)` = router as sender) and `exactOutput` (recursive callback hops call `pool.swap` with `msg.sender` = router).

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses all access control the moment the router is allowlisted. Any unpermissioned user can execute swaps against the pool's liquidity at oracle-derived prices, draining LP value through adverse selection or extracting arbitrage that the allowlist was designed to prevent. This is a direct loss of LP principal and a complete failure of the pool's core protection mechanism.

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Pool admins who deploy a curated pool with `SwapAllowlistExtension` will naturally need to allowlist the router to let their approved users trade through the standard periphery. The bypass requires no special privileges, no flash loans, and no multi-block setup — any user calls `exactInputSingle` on the router pointing at the curated pool. Likelihood is high.

---

### Recommendation

The allowlist must gate the **economic actor** (the end user), not the intermediary contract. Two complementary fixes:

1. **Pass the original initiator through the router.** Add a `recipient`-style `swapper` parameter to the router's swap functions and forward it as `callbackData` or a dedicated field so the pool can pass the true initiator to extensions. This requires a coordinated interface change.

2. **Check `recipient` or require direct-pool calls for allowlisted pools.** As a simpler short-term fix, `SwapAllowlistExtension` can check the `recipient` argument (second parameter of `beforeSwap`) instead of `sender`, since `recipient` is always the end user's address even in router-mediated flows. This is not a perfect solution (recipient ≠ payer), but it closes the router bypass for the common case.

The cleanest fix is to have the router forward `msg.sender` as an explicit `swapper` field in `extensionData`, and have `SwapAllowlistExtension` decode and check that field when present.

---

### Proof of Concept

```solidity
// Setup: pool admin creates curated pool with SwapAllowlistExtension
// Allowlists alice (direct) and the router (to enable router swaps for alice)
swapExt.setAllowedToSwap(pool, alice, true);
swapExt.setAllowedToSwap(pool, address(router), true); // ← required for router path

// Attack: bob (not allowlisted) calls through the router
vm.startPrank(bob);
token1.approve(address(router), type(uint256).max);

// Extension sees sender = router → allowedSwapper[pool][router] = true → PASSES
// Bob successfully swaps on a pool he was never meant to access
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token1),
    recipient: bob,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// Bob receives token0 from the curated pool — allowlist fully bypassed
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
