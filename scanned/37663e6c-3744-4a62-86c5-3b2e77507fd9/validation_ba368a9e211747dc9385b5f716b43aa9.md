### Title
SwapAllowlistExtension gates the router's address instead of the actual swapper, allowing any router user to bypass per-user swap restrictions — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which equals `msg.sender` of the pool's `swap` call — the router address when going through `MetricOmmSimpleRouter`. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the economically relevant party). This inconsistency is the direct analog of the external bug: one guard uses the right identity, the other uses a raw caller value. If a pool admin allowlists the router to enable router-mediated swaps, every router user bypasses the individual swap allowlist.

---

### Finding Description

**Pool passes `msg.sender` (the router) as `sender` to the extension:** [1](#0-0) 

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
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

**`SwapAllowlistExtension` checks that router-supplied `sender` value:** [2](#0-1) 

```solidity
function beforeSwap(address sender, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [3](#0-2) 

`msg.sender` inside the pool is the router, so `sender` passed to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Contrast with `DepositAllowlistExtension`, which correctly checks `owner`:** [4](#0-3) 

```solidity
function beforeAddLiquidity(address, address owner, ...)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

`owner` is preserved correctly because `MetricOmmPoolLiquidityAdder` explicitly passes the position owner to `pool.addLiquidity(positionOwner, ...)`: [5](#0-4) 

The deposit allowlist gates the economically relevant party; the swap allowlist gates the direct caller. This is the exact structural inconsistency described in the external report.

---

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified users) and also allowlists the router to enable router-mediated swaps for those users inadvertently opens the pool to **all** router users. Any non-allowlisted address can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The admin-configured access control is silently defeated, allowing unauthorized parties to trade in a pool intended to be restricted.

---

### Likelihood Explanation

Medium. The pool admin must allowlist the router address to let allowlisted users trade via the router — a natural operational step. The inconsistency between how deposit and swap allowlists gate identity (owner vs. sender) makes this easy to overlook. No privileged attacker role is required; any public user can exploit it once the router is allowlisted.

---

### Recommendation

1. **Preferred fix**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` (the actual user) into `extensionData` and have `SwapAllowlistExtension` decode and verify it, analogous to how `owner` is passed explicitly in `addLiquidity`.
2. **Alternative**: Add an explicit `swapper` parameter to the pool's `swap` function (mirroring `owner` in `addLiquidity`) so extensions can gate the economically relevant party rather than the direct caller.
3. **Minimum**: Document clearly that allowlisting the router grants swap access to **all** router users, not just individually allowlisted addresses, so pool admins understand the trade-off.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` attached to the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is individually allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — `msg.sender` inside the pool is the router.
6. Pool calls `_beforeSwap(router, ...)` — `sender` = router address.
7. Extension evaluates `allowedSwapper[pool][router]` = `true` → no revert.
8. Bob's swap executes successfully in a pool he was never individually allowlisted for, bypassing the intended access control. [2](#0-1) [6](#0-5) [7](#0-6) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
