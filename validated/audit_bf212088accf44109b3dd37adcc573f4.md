### Title
`SwapAllowlistExtension` checks router address instead of end user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users swap through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so the extension checks the router's address — not the end user's address. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the allowlist to every user on the network.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with its own `msg.sender` as the first argument: [1](#0-0) 

That value flows through `ExtensionCalling._beforeSwap` unchanged: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks that `sender` value.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` inside the extension is the pool; `sender` is whoever called `pool.swap()`. [3](#0-2) 

**Step 3 — The router calls `pool.swap()` directly, substituting itself for the user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` with no mechanism to forward the originating user's address: [4](#0-3) 

The pool therefore receives `msg.sender == router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

**The forced dilemma for the pool admin:**

| Admin choice | Consequence |
|---|---|
| Allowlist the router address | Every user on the network can swap through the router; per-user allowlist is nullified |
| Do not allowlist the router | Every allowlisted user is blocked from using the router; core swap path is broken |

Neither option preserves the intended invariant. The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position owner explicitly passed by the caller), not `sender`: [5](#0-4) 

`SwapAllowlistExtension` has no equivalent forwarded identity field.

---

### Impact Explanation

If the pool admin allowlists the router (the only way to let users swap through the standard periphery), the `SwapAllowlistExtension` becomes a no-op for all router-mediated swaps. Any unprivileged user can bypass the allowlist by calling `router.exactInputSingle` or any other router entry point. This breaks the core security invariant the extension is designed to enforce — e.g., KYC gating, counterparty restrictions, or LP-protection allowlists — and constitutes broken core pool functionality with direct fund-impact potential (unauthorized traders accessing restricted pools, LP losses from adversarial swappers the allowlist was meant to exclude).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary production swap entry point. Any pool that deploys `SwapAllowlistExtension` and also wants users to be able to use the router faces this dilemma immediately. The bypass requires no special privileges: any user calls the public router with a standard `exactInputSingle` call.

---

### Recommendation

1. **Preferred fix**: Add an `initiator` field to the `beforeSwap` hook signature (or pass it via `extensionData`) so the extension can check the originating user rather than the direct pool caller.
2. **Short-term mitigation**: Document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and must only be used with direct pool calls where `msg.sender == end user`.
3. **Alternative**: Mirror the `DepositAllowlistExtension` pattern — have the router forward the originating user's address in `extensionData`, and have the extension decode and check that value.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowlisted.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router must be allowlisted for any router swap.
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
   → pool.swap() is called with msg.sender == router.
   → extension checks allowedSwapper[pool][router] == true → passes.
   → Bob's swap executes despite not being on the allowlist.

Alternatively (no router allowlist):
3b. Pool admin does NOT allowlist the router.
4b. Alice (allowlisted) calls router.exactInputSingle({pool: pool, ...}).
    → extension checks allowedSwapper[pool][router] == false → reverts NotAllowedToSwap.
    → Alice cannot use the standard periphery even though she is individually allowlisted.
``` [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-29)
```text
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
