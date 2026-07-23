### Title
SwapAllowlistExtension Checks the Router Address Instead of the Actual End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps), every user — including those not on the allowlist — can bypass the swap gate by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` as `msg.sender`: [4](#0-3) 

So the pool receives `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`. The actual end user who called the router is never inspected.

This creates an inescapable dilemma for pool admins:

| Router allowlisted? | Effect |
|---|---|
| No | Router-mediated swaps revert for **everyone** — the router itself is blocked |
| Yes | **Any** user can bypass the allowlist by routing through the router |

There is no configuration that allows "router-mediated swaps for allowlisted users only."

**Contrast with `DepositAllowlistExtension`:** the deposit path explicitly passes `owner` (the actual position holder) as a separate argument, and the extension checks `owner` — not `sender`. The liquidity adder forwards the real user as `owner`, so the deposit allowlist correctly gates the economically relevant actor regardless of the intermediary: [5](#0-4) 

The swap path has no equivalent explicit-user parameter; `recipient` is the output-token destination, not the initiating user.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` (e.g., for KYC compliance, institutional-only access, or regulatory restrictions) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The allowlist provides zero protection against router-mediated swaps once the router is allowlisted. Unauthorized users gain full swap access to a curated pool, defeating the curation policy entirely — a direct curation failure and admin-boundary break.

### Likelihood Explanation

Medium-High. Any pool admin who wants to support the standard periphery router must allowlist it. The moment they do, the allowlist is open to all users. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call suffices.

### Recommendation

Pass the actual initiating user through the swap path so the extension can gate the economically relevant actor. Two approaches:

1. **Add an explicit `user` parameter to `pool.swap()`** (analogous to `owner` in `addLiquidity()`). The router would forward `msg.sender` as `user`; the extension would check `allowedSwapper[pool][user]`.

2. **Check `recipient` instead of `sender`** in `SwapAllowlistExtension` — only viable if the pool admin's intent is to gate who receives output tokens, not who initiates the swap.

Approach 1 is the correct structural fix and mirrors how `DepositAllowlistExtension` correctly gates `owner` rather than `sender`.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin enables router-mediated swaps
  allowedSwapper[pool][alice]  = true   // alice is the only intended user
  bob is NOT on the allowlist

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)  [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
    → swap executes for bob with no revert

Result:
  bob, a non-allowlisted user, successfully swaps on a curated pool.
  The allowlist is completely ineffective for router-mediated swaps.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
