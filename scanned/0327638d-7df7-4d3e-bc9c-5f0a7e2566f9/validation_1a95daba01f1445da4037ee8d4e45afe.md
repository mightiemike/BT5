### Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any user to bypass the swap allowlist gate — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `swap` function passes `msg.sender` (the router address) as `sender` to the extension. If the pool admin allowlists the router to enable router-mediated swaps, every user — including non-allowlisted ones — can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` always passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...))   // sender = router address
);
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded address against its allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The check resolves to `allowedSwapper[pool][router]`.

A pool admin who wants to allow router-mediated swaps must add the router to the allowlist. Once the router is allowlisted, the guard passes for **every** caller of `router.exactInputSingle` / `router.exactInput` / `router.exactOutputSingle`, regardless of whether the actual end-user is on the allowlist.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` argument (the position owner), not `sender`, so it gates the economically relevant actor even when the `MetricOmmPoolLiquidityAdder` is the `msg.sender` of `addLiquidity`. The swap extension has no equivalent forwarding of the real user identity.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. Once the router is allowlisted (the only way to support the standard periphery flow), the allowlist is completely ineffective: any address can call `router.exactInputSingle(...)` and the extension will approve the swap because it sees the allowlisted router, not the actual caller. Non-allowlisted users can drain LP funds from a pool that was designed to be access-controlled.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and tested throughout the periphery. Any pool admin who configures `SwapAllowlistExtension` and also wants users to be able to use the router (the normal flow) will add the router to the allowlist, unknowingly opening the gate to all users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — a single standard router call suffices.

---

### Recommendation

Pass the original end-user address through the swap path so the extension can gate the economically relevant actor. Two options:

1. **Add a `swapper` field to the swap call**: Extend `IMetricOmmPoolActions.swap` with an explicit `swapper` address (defaulting to `msg.sender` for direct calls) and pass that to `_beforeSwap` instead of `msg.sender`. The router would supply `msg.sender` (the actual user) at call time.

2. **Mirror the deposit pattern**: Follow the same design as `DepositAllowlistExtension`, which checks `owner` (the position owner) rather than `sender` (the payer/caller). For swaps, the equivalent is the `recipient` or a separately supplied `swapper` address that the router sets to the real user.

Until fixed, pool admins should not rely on `SwapAllowlistExtension` for per-user access control on pools that are also accessible through the router.

---

### Proof of Concept

**Setup**:
- Pool configured with `SwapAllowlistExtension`
- `alice` is NOT in the allowlist
- `MetricOmmSimpleRouter` IS in the allowlist (admin added it to support router-mediated swaps)

**Attack**:
```
alice → router.exactInputSingle({pool: curated_pool, tokenIn: token0, ...})
       → pool.swap(recipient=alice, ...)   [msg.sender = router]
       → _beforeSwap(sender=router, ...)
       → SwapAllowlistExtension.beforeSwap(sender=router, ...)
       → allowedSwapper[pool][router] == true  ✓  (passes!)
       → alice's swap executes against the curated pool
```

**Expected behavior**: `NotAllowedToSwap` revert because `alice` is not allowlisted.
**Actual behavior**: Swap succeeds because the router is allowlisted and the extension never sees `alice`'s address.

**Key code references**: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
