### Title
Swap Allowlist Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed), every user — including those not on the allowlist — can bypass the guard by routing through the public router.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to the extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that `sender` and calls the configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][immediate_caller_of_pool_swap]`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the one that calls `pool.swap()`: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the actual user. The pool admin must allowlist the router for any router-mediated swap to pass the guard. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call, and every user — regardless of their individual allowlist status — can swap freely through the router.

The `DepositAllowlistExtension` does not share this flaw because `addLiquidity` takes an explicit `owner` parameter that the pool passes through to the extension, correctly identifying the economic actor: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` (e.g., for KYC-only trading or institutional access) and allowlists the router to support normal user flows inadvertently opens the gate to all users. Any non-allowlisted address can bypass the guard by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). This defeats the entire purpose of the allowlist and constitutes a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses an admin-configured access control guard.

---

### Likelihood Explanation

The router is the primary user-facing entry point documented and deployed for the protocol. Pool admins who want legitimate users to swap through the router must allowlist it. The bypass is therefore reachable on any allowlisted pool that also permits router access, which is the expected production configuration. No special privileges or malicious setup are required — any EOA can call the public router.

---

### Recommendation

The extension must gate the economically relevant actor, not the immediate caller of `pool.swap()`. Options:

1. **Pass the original user through the router**: Have the router encode the original `msg.sender` in `extensionData` and have the extension decode and check it. This requires a coordinated change to both the router and the extension.
2. **Check `recipient` as a proxy**: If the pool admin's intent is to gate who receives output tokens, check `recipient` instead of `sender`. This is semantically different but may match the intended policy.
3. **Document the limitation**: If the allowlist is only intended for direct `pool.swap()` callers (not router users), document explicitly that the router must not be allowlisted and that router-mediated swaps are ungated.

The cleanest fix is option 1: the router should forward the original `msg.sender` in a standardized `extensionData` field, and the extension should verify and use it.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for any router swap
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  ✓ passes
  - Swap executes for attacker despite not being on the allowlist

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
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
