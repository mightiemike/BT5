### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router (a natural configuration to let their curated users use the standard periphery UX), every unprivileged user can bypass the per-user allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` value and dispatches it to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the router, so `sender` delivered to the extension is the router address — not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][endUser]`.

A pool admin who wants their allowlisted users to be able to use the standard router UX will naturally add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every caller, and the per-user gate is completely bypassed.

By contrast, `DepositAllowlistExtension` correctly gates by the `owner` argument (the position owner), which is the same regardless of whether the call comes through the liquidity adder or directly: [5](#0-4) 

The asymmetry is structural: deposit gating keys on the economically relevant identity (`owner`), while swap gating keys on the transport-layer identity (`sender = direct caller of pool.swap()`).

### Impact Explanation

Any unprivileged user can swap on a pool that the admin intended to restrict to a curated set of counterparties. Depending on the pool's purpose this can mean:

- Unauthorized access to a compliance-gated or KYC-restricted pool.
- Unauthorized extraction of liquidity at oracle-anchored prices from a pool whose LPs deposited under the assumption that only trusted counterparties would trade against them.
- Direct loss of LP principal if the pool was designed to limit adverse selection to a known set of actors.

This matches the "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact gate.

### Likelihood Explanation

The trigger is a routine, well-documented configuration choice. The `MetricOmmSimpleRouter` is the standard swap entry point for end users. A pool admin who deploys a curated pool and wants their allowlisted users to use the router will add the router to the allowlist — this is the only way to make the router work for those users. The bypass is therefore reachable whenever the pool admin makes the natural configuration decision to support the periphery UX. No malicious admin action is required; the admin is acting in good faith.

### Recommendation

Gate the allowlist on the economically relevant actor, not the transport-layer caller. Two concrete options:

1. **Pass the original `msg.sender` through the router.** Have `MetricOmmSimpleRouter` forward the end user's address in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of `sender`. This requires a convention between the router and the extension.

2. **Check `sender` only when `sender` is not a known router; otherwise decode the real user from `extensionData`.** The extension can maintain a registry of trusted routers and fall back to the payload-encoded user identity for those callers.

Either approach mirrors how `DepositAllowlistExtension` already correctly keys on `owner` (the position owner) rather than the transport-layer `sender`.

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension in beforeSwap slot
  pool admin calls setAllowedToSwap(pool, alice, true)
  pool admin calls setAllowedToSwap(pool, router, true)   // natural: let alice use the router
  bob is NOT in the allowlist

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → checks allowedSwapper[pool][router] → true
  → swap proceeds; bob successfully trades on the curated pool

Expected: revert NotAllowedToSwap
Actual:   swap executes; allowlist is bypassed
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
