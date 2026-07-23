### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Per-Pool Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address, not the actual end user. If the pool admin allowlists the router (a natural step to support router-mediated swaps for allowlisted users), every unprivileged user can bypass the allowlist by routing through the router.

### Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is exactly `msg.sender` of the `pool.swap()` call:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` inside the extension is the pool (correct), and `sender` is whoever called `pool.swap()`. In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol L230-239
_beforeSwap(
    msg.sender,   // ← this is the router when the user goes through the router
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

So `msg.sender` to the pool is the router, and the extension checks `allowedSwapper[pool][router]`. The actual end user's address is never checked.

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. But allowlisting the router address means **every user** who calls the router can bypass the allowlist, because the extension cannot distinguish between different callers of the same router.

### Impact Explanation
Any unprivileged user can bypass the swap allowlist on any pool that has allowlisted the `MetricOmmSimpleRouter`. If the allowlist was deployed to protect LP funds from adverse selection (e.g., only trusted market makers may trade), the bypass allows arbitrary users to drain LP value through unfavorable swaps. The allowlist invariant — that only explicitly approved addresses may swap — is completely broken for all router-mediated swaps once the router is allowlisted.

### Likelihood Explanation
Medium. The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected configuration step: any admin who wants their allowlisted users to be able to use the standard periphery router must allowlist it. The admin has no way to achieve "allowlisted users can use the router" without simultaneously granting "all users can use the router," because the extension has no mechanism to inspect the original `msg.sender` of the router call. Once the router is allowlisted, the bypass is unconditional and requires no special privileges from the attacker.

### Recommendation
The `SwapAllowlistExtension` should gate on the economically relevant actor, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: The router passes the original `msg.sender` in `extensionData`; the extension decodes and checks it. This requires the router to be trusted to forward the correct address.
2. **Separate router-aware allowlist**: Maintain a mapping of trusted routers and, when `sender` is a known router, decode the actual user from `extensionData` before checking the allowlist.

The simplest safe default is to document that allowlisting the router grants access to all router users, and provide a router variant that forwards the original caller so the extension can check the correct identity.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, user1, true)` — intending only `user1` to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to let `user1` use the router.
4. `user2` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `user2`'s swap executes successfully, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
