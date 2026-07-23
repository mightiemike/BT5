### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every unprivileged user can bypass the individual-user allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- pool's caller, i.e. the router when routed
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

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every registered extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified,
         priceLimitX64, packedSlot0Initial, bidPriceX64, askPriceX64, extensionData))
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates an irreconcilable dilemma for pool admins:

- **If the router is NOT allowlisted**: even allowlisted users cannot swap through the router; they must call the pool directly, breaking the expected periphery UX.
- **If the router IS allowlisted** (the only way to enable router-mediated swaps): every unprivileged user can bypass the individual-user allowlist by routing through the router, because the extension sees only the router address.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. An unauthorized user calls `exactInputSingle` (or any router entry point) targeting the allowlisted pool; the pool sees `msg.sender = router`; the extension checks `allowedSwapper[pool][router]`; if the router is allowlisted, the swap executes. The curated pool's access control is fully bypassed, allowing unauthorized parties to trade against LP positions that were intended to be protected. This constitutes a broken core pool functionality and an admin-boundary break where an unprivileged path circumvents a configured guard.

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and also wants to support the official `MetricOmmSimpleRouter` must allowlist the router. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges or capital requirements. The likelihood is medium because it requires the pool admin to have allowlisted the router, which is the natural configuration for any pool that intends to support router-mediated swaps.

### Recommendation

The `SwapAllowlistExtension` should gate the **economic actor** rather than the immediate caller. Two approaches:

1. **Pass the original user through the router**: The router could forward the end-user address in `extensionData`, and the extension could decode and check it. However, this requires a coordinated change to both the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to forward user identity**: Add a convention where the router encodes the originating user in `extensionData`, and the extension decodes it when `sender` is a known router.

3. **Simplest fix**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and that pools using it must not allowlist the router. Alternatively, provide a separate extension variant that reads the user from `extensionData` when the immediate caller is a trusted router.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists alice directly: allowedSwapper[pool][alice] = true
  - Admin allowlists the router: allowedSwapper[pool][router] = true
    (required so alice can use the router)

Attack (charlie, not allowlisted):
  1. charlie calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(...) — pool sees msg.sender = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes for charlie despite charlie not being allowlisted

Result:
  - charlie successfully swaps on a pool that was supposed to restrict trading to alice only
  - The allowlist policy is completely bypassed for any user who routes through the router
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
