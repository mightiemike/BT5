### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass Per-User Swap Restrictions via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router address, not the end user. If the pool admin allowlists the router to support router-mediated swaps, every user of the permissionless router bypasses the per-user restriction, nullifying the curation policy.

### Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as the first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is the value the pool passes, which is `msg.sender` of the pool's own `swap()` call:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`, so the pool passes `msg.sender = router` as `sender` to the extension. The allowlist check becomes `allowedSwapper[pool][router]`.

A pool admin who wants to support both direct pool calls (for specific allowlisted users) and router-mediated swaps must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the permissionless router — including users who are not individually allowlisted. The extension cannot distinguish between different end users who all route through the same router contract.

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any address can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. There is no per-user gate inside the router itself.

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of users (e.g., institutional market makers, KYC'd participants) has its curation policy completely bypassed for router-mediated swaps once the router is allowlisted. Any unprivileged user can execute swaps against the pool's LP liquidity, which the pool admin explicitly intended to restrict. This constitutes a broken core pool functionality (the allowlist guard fails open) and can cause direct loss to LPs if the pool was designed to trade only with trusted counterparties.

### Likelihood Explanation
Medium. The bypass requires the admin to allowlist the router address. This is a natural and expected administrative action: a pool admin who wants their allowlisted users to be able to use the standard periphery router would allowlist it. The admin may not realize that allowlisting the router — a permissionless public contract — opens the pool to all users. The `SwapAllowlistExtension` documentation ("Gates `swap` by swapper address, per pool") does not warn that the "swapper" is the immediate caller of `pool.swap()`, not the economic end user.

### Recommendation
The extension should gate the economic end user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass end-user identity through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension verifies a signature or trusted-forwarder proof binding that address to the swap. This requires router cooperation and a trust model for the forwarded identity.

2. **Reject router-mediated swaps entirely**: Document that pools using `SwapAllowlistExtension` must not allowlist the router address, and that router-mediated swaps are incompatible with per-user allowlisting. Add a NatSpec warning to `setAllowedToSwap` and `setAllowAllSwappers`.

3. **Dual-check**: Require both the immediate caller and an end-user address (supplied via `extensionData`) to be allowlisted, with the router being responsible for forwarding the real user's address in a tamper-evident way.

### Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, user1, true)       // allowlist user1 for direct calls
  admin calls setAllowedToSwap(pool, router, true)      // allowlist router to support periphery

Attack:
  user2 (not individually allowlisted) calls:
    router.exactInputSingle(ExactInputSingleParams{
        pool: pool,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: X,
        amountOutMinimum: 0,
        ...
    })

  Execution trace:
    router → pool.swap(recipient=user2, zeroForOne=true, ...)
    pool   → _beforeSwap(sender=router, ...)
    pool   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    extension checks: allowedSwapper[pool][router] == true  ✓
    swap executes — user2 bypasses the per-user allowlist
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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
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
