### Title
SwapAllowlistExtension gates the router address instead of the real swapper, allowing any user to bypass the curated-pool swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user enters through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the extension checks `allowedSwapper[pool][router]` instead of the actual user's address. A pool admin who allowlists the router to enable standard router-mediated swaps inadvertently opens the allowlist to every user on the network.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to each extension in the configured order: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses `msg.sender` (the pool) as the mapping key and the received `sender` argument as the swapper identity: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that call site `msg.sender` of the pool is the router contract. The actual user's address is stored only in the transient callback context (`_setNextCallbackContext`) for payment settlement and is never forwarded to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`.

This creates an irreconcilable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

- **Option A – do not allowlist the router:** every user who tries to swap through the standard periphery path gets `NotAllowedToSwap`, making the router unusable on that pool.
- **Option B – allowlist the router:** the check becomes `allowedSwapper[pool][router] == true` for every swap that enters through the router, so the allowlist is completely bypassed for the entire public.

There is no configuration that simultaneously allows router-mediated swaps for specific users and blocks them for others.

### Impact Explanation
Any user can bypass the swap allowlist on a curated pool by routing through the public `MetricOmmSimpleRouter`. If the allowlist was deployed to restrict trading to specific market makers or counterparties (a common use case for oracle-anchored pools), the bypass lets arbitrary actors trade against LP positions at oracle prices, potentially extracting value from LPs in ways the pool admin explicitly intended to prevent. This is a direct loss-of-LP-principal path reachable by any unprivileged user.

### Likelihood Explanation
The `MetricOmmSimpleRouter` is the standard, documented swap entry point for end users. A pool admin who wants to allow allowlisted users to trade through the normal UX will naturally allowlist the router. The bypass requires no special knowledge: any user who observes that the pool has a swap allowlist simply calls the router instead of the pool directly. The trigger is a single public transaction with no preconditions beyond token approval.

### Recommendation
The extension must check the economically relevant actor, not the immediate caller of the pool. Two viable approaches:

1. **Pass the real user through `extensionData`:** Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires the router to be trusted to supply the correct value, which can be enforced by checking that `msg.sender` (the pool's caller) is a known factory-registered router.

2. **Check `sender` only when the caller is not a trusted router:** Maintain a factory-level registry of trusted routers; when `sender` is a trusted router, decode the real user from `extensionData`; otherwise use `sender` directly.

Either approach must be applied symmetrically to `DepositAllowlistExtension` if the same pattern is used for liquidity adder paths.

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap extension
  admin calls extension.setAllowedToSwap(pool, router, true)
    // admin intends to allow router-mediated swaps for allowlisted users
  alice = allowlisted EOA
  bob   = non-allowlisted EOA

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    // router calls pool.swap(...) — msg.sender of pool = router
    // pool calls extension.beforeSwap(sender=router, ...)
    // extension checks allowedSwapper[pool][router] == true  ✓
    // swap executes — bob bypassed the allowlist

Verification:
  bob calls pool.swap(...) directly
    // pool calls extension.beforeSwap(sender=bob, ...)
    // extension checks allowedSwapper[pool][bob] == false  → NotAllowedToSwap ✓
```

The direct call correctly blocks `bob`, but the router path succeeds because the extension sees the router's address, not `bob`'s. The allowlist invariant is broken for every user who routes through the public periphery.

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
