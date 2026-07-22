### Title
SwapAllowlistExtension gates on the router address instead of the end user, enabling allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router to support router-mediated swaps for their curated users, every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

The `SwapAllowlistExtension` is intended to gate swaps by swapper identity. Its `beforeSwap` hook receives `sender` as the first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the caller of the extension) and `sender` is the first argument the pool passes — which is `msg.sender` of the `pool.swap()` call:

```solidity
_beforeSwap(
  msg.sender,   // ← always the immediate caller of pool.swap()
  recipient,
  ...
)
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with `msg.sender = router`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The pool therefore passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**The asymmetry with `DepositAllowlistExtension`:** The deposit extension correctly gates on the `owner` argument — the position owner explicitly passed by the caller — so the `MetricOmmPoolLiquidityAdder` can forward the real user's address. The `swap` function has no equivalent explicit `sender` parameter; it always uses `msg.sender`. [4](#0-3) 

**Consequence:** A pool admin who wants to support router-mediated swaps for their allowlisted users must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every user who routes through it — including users who are individually blocked. The extension has no mechanism to distinguish end users behind the same router address. [5](#0-4) 

---

### Impact Explanation

Any user blocked by the per-user allowlist can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) on a pool whose admin has allowlisted the router. The curated-pool policy is fully defeated: unauthorized users trade on pools they were explicitly excluded from. This is a direct broken-core-functionality impact — the allowlist is the primary access-control mechanism for curated pools, and it fails open for the entire router-mediated path.

---

### Likelihood Explanation

The trigger requires the pool admin to add the router to the allowlist. This is a natural and expected configuration: any pool admin who wants their allowlisted users to benefit from router UX (slippage protection, multi-hop, deadline checks) must allowlist the router. The admin has no alternative — the pool's `swap` signature provides no way to forward the real user's identity. The mistake is therefore predictable and not contrived.

---

### Recommendation

Align the swap allowlist with the deposit allowlist's design: gate on the economically relevant actor, not the immediate caller.

- **Short-term:** Document that allowlisting the router grants access to all router users, and advise pool admins to allowlist individual users for direct `pool.swap()` calls only.
- **Long-term:** Extend the pool's `swap` function with an explicit `sender` parameter (analogous to `addLiquidity`'s `owner`), allowing the router to forward the real user's address. The `SwapAllowlistExtension` would then check that forwarded address instead of `msg.sender` of the pool call.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router to support Alice's router swaps: `setAllowedToSwap(pool, router, true)`.
4. Bob (not individually allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` — `msg.sender` of the pool is `router`.
6. The pool calls `extension.beforeSwap(router, ...)` — `sender = router`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
8. Bob's swap executes successfully, bypassing the per-user allowlist entirely. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
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
