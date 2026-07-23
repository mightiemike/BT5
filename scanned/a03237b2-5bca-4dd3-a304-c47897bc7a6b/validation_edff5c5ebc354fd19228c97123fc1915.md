### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via the Supported Router Path — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its per-pool allowlist against `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the user's address. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the pool to every user, completely defeating the per-user curation the extension was designed to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the router is the direct caller of `pool.swap()`, so `sender` received by the extension is always the router address, never the originating user.

This creates an irreconcilable dilemma for any pool admin who wants to run a curated pool while still supporting the official router:

- **Option A — do not allowlist the router**: allowlisted users cannot swap through the router at all; they must call the pool directly.
- **Option B — allowlist the router**: the check becomes `allowedSwapper[pool][router] == true`, which passes for every user regardless of their individual allowlist status, because the router is a shared public contract.

Option B is the natural operational choice (the router is the documented user-facing entry point), yet it silently removes all per-user gating.

The asymmetry with `DepositAllowlistExtension` makes this especially confusing: the deposit extension correctly gates the economically relevant actor by checking `owner` (the position owner, explicitly passed through the call chain), while the swap extension gates the intermediary contract instead of the user: [5](#0-4) 

---

### Impact Explanation

Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter` once the pool admin has allowlisted the router address. The pool's curation policy is completely nullified: unauthorized counterparties can trade against LP positions that were provisioned under the assumption of a restricted, known set of swappers. This constitutes a broken core pool functionality with direct potential for LP fund loss on curated pools.

---

### Likelihood Explanation

The likelihood is high. The router is the primary user-facing entry point documented and deployed by the protocol. A pool admin who deploys a curated pool and then enables router access (a routine operational step) will unknowingly open the pool to all users. The misconfiguration requires no attacker privilege — any public user can exploit it by simply calling `exactInputSingle` on the router targeting the allowlisted pool.

---

### Recommendation

The swap allowlist must gate the originating user, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` as an authenticated field in `extensionData` (signed or verified via a trusted forwarder pattern), and `SwapAllowlistExtension` should decode and check that field when `sender` is a known router.

2. **Alternatively, check `recipient` or add a dedicated `originator` field to the swap interface**: the pool could accept an explicit `originator` address that the router populates with `msg.sender`, and extensions gate on that field.

Until fixed, pool admins should be warned that allowlisting the router address is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)  // only alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true) // enable router for alice

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
        pool, tokenIn, tokenOut, amountIn, ..., recipient=bob
    )
  - Router calls pool.swap(bob, ...) — msg.sender of pool.swap() = router
  - _beforeSwap passes sender = router to SwapAllowlistExtension
  - Check: allowedSwapper[pool][router] == true  → passes
  - bob's swap executes successfully despite not being on the allowlist

Result:
  - bob trades against LP positions provisioned for a curated counterparty set.
  - The allowlist is completely bypassed for any user who routes through the router.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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
