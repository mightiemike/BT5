### Title
`SwapAllowlistExtension` gates by router address instead of actual user when swaps route through `MetricOmmSimpleRouter`, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router address to enable router-based swaps, every user on the network can bypass the per-user allowlist and trade in a curated pool.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract address**: [4](#0-3) 

Therefore the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. A pool admin who wants router-based swaps to work at all must allowlist the router address. Once the router is allowlisted, **every caller of the router** passes the check, regardless of whether that individual user was ever approved.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by the explicitly supplied `owner` argument (the position owner), which cannot be spoofed by routing through an intermediary: [5](#0-4) 

The asymmetry between the two extensions is the root cause: deposit allowlisting is actor-stable across periphery paths; swap allowlisting is not.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for per-user curation (e.g., KYC-only trading, institutional-only pools, or whitelist-gated liquidity programs) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege — they only need to call the public router. The pool admin cannot simultaneously (a) allow legitimate allowlisted users to use the router and (b) block non-allowlisted users from using the router, because both groups appear to the extension as the same `sender` (the router address). This breaks the core curation invariant of the extension and allows unauthorized users to extract value from pools that were designed to restrict trading.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point for the protocol. Any pool that deploys `SwapAllowlistExtension` with per-user allowlisting and expects users to interact via the router will be affected. The pool admin has no on-chain mechanism to distinguish individual users once the router is allowlisted. The bypass requires only a standard router call — no flash loans, no reentrancy, no privileged access.

---

### Recommendation

Gate the swap allowlist on the **end user** rather than the immediate pool caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router binding (the extension must verify `msg.sender` is a known router before trusting the payload).

2. **Mirror the deposit extension pattern**: Add an explicit `swapper` parameter to the pool's swap interface (analogous to `owner` in `addLiquidity`) that the caller must supply and that the pool enforces matches `msg.sender` or a delegated address. The extension then checks that field instead of `sender`.

Until fixed, pool admins should be warned that allowlisting the router address grants unrestricted swap access to all router users, and that per-user swap gating only works when users call the pool directly.

---

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E configured on beforeSwap.
2. Admin allowlists only address Alice: setAllowedToSwap(P, Alice, true).
3. Admin also allowlists the router to enable router-based swaps:
       setAllowedToSwap(P, router, true).
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: P, ...})
5. Router calls pool.swap(...) — pool's msg.sender = router.
6. Pool calls E.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[P][router] == true → passes.
8. Bob's swap executes in the curated pool despite never being allowlisted.
```

Alternatively, if the admin does **not** allowlist the router:

```
1. Admin allowlists Alice: setAllowedToSwap(P, Alice, true).
2. Alice calls router.exactInputSingle({pool: P, ...}).
3. Router calls pool.swap() — pool's msg.sender = router.
4. Extension checks allowedSwapper[P][router] == false → reverts NotAllowedToSwap.
5. Alice cannot use the router at all, even though she is individually allowlisted.
```

Both outcomes demonstrate that the configured guard is misapplied across the supported periphery path.

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
