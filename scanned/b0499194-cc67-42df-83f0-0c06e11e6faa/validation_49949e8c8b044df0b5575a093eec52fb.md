### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for curated pools), every user — including those not on the allowlist — can bypass the gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with `msg.sender = router`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, the check passes for **every** caller regardless of their individual allowlist status, because the extension cannot distinguish between different users behind the router.

The same structural problem exists for `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` as `msg.sender = router`: [5](#0-4) 

---

### Impact Explanation

A curated pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a specific set of addresses. Once the router is allowlisted (the only way to let allowlisted users trade through the standard periphery), the allowlist is completely nullified for all router-mediated swaps. Any unprivileged user can execute swaps on the curated pool, bypassing the admin-configured access boundary. This is a direct admin-boundary break: an unprivileged path (the public router) defeats a pool-admin-configured guard, allowing unauthorized trading that the pool admin explicitly intended to prevent.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin deploying a curated pool with `SwapAllowlistExtension` who also wants allowlisted users to access the router will inevitably allowlist the router address — this is the only supported path to enable router-mediated swaps. The misconfiguration is not a mistake; it is the only available configuration that enables router access at all, making the bypass reachable in any realistic curated-pool deployment.

---

### Recommendation

The extension must identify the **economic actor**, not the intermediary. Two approaches:

1. **Pass the original caller through the router.** The router could forward the original `msg.sender` in `extensionData`, and the extension could decode and check it. This requires a protocol-level convention.
2. **Check `recipient` instead of `sender` for swap allowlists**, if the intent is to gate who receives output tokens. For input-side gating, the payer identity must be threaded through the callback context and exposed to extensions.
3. **Document incompatibility.** At minimum, the extension and pool configuration docs must warn that allowlisting the router defeats per-user gating, and that direct-pool-only access is the only safe configuration for curated pools.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// allowlist only Alice
ext.setAllowedToSwap(pool, alice, true);
// allowlist the router so Alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// Bob (not allowlisted) routes through the router
vm.startPrank(bob);
usdc.approve(address(router), type(uint256).max);
// extension checks allowedSwapper[pool][router] == true → passes
// Bob successfully swaps on the curated pool
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000e6,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
vm.stopPrank();
// Bob's swap succeeds despite not being on the allowlist
```

The extension evaluates `allowedSwapper[pool][router]` (true) rather than `allowedSwapper[pool][bob]` (false), so the guard passes and Bob's swap executes on the curated pool. [3](#0-2) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
