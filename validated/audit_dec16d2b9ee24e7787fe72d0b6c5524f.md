Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address against the allowlist rather than the actual end user's address. If the pool admin allowlists the router to give allowlisted users standard UX access, every unprivileged user can bypass the per-user allowlist by routing through the same router contract.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` — the economically relevant actor — rather than `sender`, making it immune to this class of bypass: [5](#0-4) 

This asymmetry is the root cause. The deposit guard keys on the position owner regardless of who called the pool; the swap guard keys on the immediate pool caller, which changes depending on the entry path.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an irreconcilable choice: either the router is not allowlisted (breaking standard UX for allowlisted users, since their address is not `sender` and every router-mediated swap reverts with `NotAllowedToSwap`), or the router is allowlisted (allowing every unprivileged user to bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`). In the latter case, non-allowlisted users gain full swap access to a pool designed to restrict access. On a curated pool with concentrated liquidity at oracle-anchored prices, unrestricted swappers can extract value from LP positions priced assuming a controlled counterparty set, constituting a direct loss of LP principal. This is a broken core pool functionality causing loss of funds.

## Likelihood Explanation
The trigger is unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The only prerequisite is that the pool admin has allowlisted the router — a natural and expected action since the router is the protocol's primary user-facing entry point and allowlisted users need it for standard UX. The admin has no way to achieve per-user allowlisting and router access simultaneously, making the bypass reachable in any realistic curated-pool deployment.

## Recommendation
Change `SwapAllowlistExtension.beforeSwap` to mirror `DepositAllowlistExtension.beforeAddLiquidity` by checking a canonical "swapper identity" independent of the immediate caller. The router should forward the originating user's address as part of `extensionData` with a verifiable mechanism (e.g., a signed attestation or a trusted forwarder pattern), and the extension should decode and check that address. Alternatively, add an explicit `swapper` field to the swap call signature so the pool can pass the true end user to extensions. As a minimal mitigation, document clearly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and that per-user allowlisting is only enforceable on direct `pool.swap()` calls.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the intended allowlisted user)
  - allowedSwapper[pool][router] = true  (admin sets this so alice can use the router)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient=bob, ...)
  - pool calls _beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] == true → passes
  - bob's swap executes on the curated pool

Contrast (direct call):
  - bob calls pool.swap() directly → sender=bob → allowedSwapper[pool][bob]=false → reverts

Result:
  - bob, who is not in the allowlist, successfully swaps on a pool
    designed to restrict access to alice only, by routing through MetricOmmSimpleRouter.
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
