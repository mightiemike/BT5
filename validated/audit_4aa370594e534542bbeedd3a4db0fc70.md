Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` at the pool — the router contract, not the originating user — when swaps are routed through `MetricOmmSimpleRouter`. If the pool admin allowlists the router (the only way to let allowlisted users use the standard periphery), the check passes for every caller of the router, completely nullifying the allowlist.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router when routing through `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router `msg.sender` at the pool: [4](#0-3) 

The result is that `allowedSwapper[pool][router]` is evaluated, not `allowedSwapper[pool][actualUser]`. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` argument (the position beneficiary), not `sender`: [5](#0-4) 

This asymmetry confirms the swap-side check is defective. No existing guard prevents a non-allowlisted user from calling `router.exactInputSingle()` targeting a curated pool once the router is allowlisted.

## Impact Explanation
A pool using `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses. Once the pool admin allowlists the router — a necessary step for any allowed user who wants to use the standard periphery — the allowlist provides zero protection. Any address on the network can call `MetricOmmSimpleRouter.exactInputSingle()` targeting the pool and the `beforeSwap` hook will pass. This is a broken core pool functionality / admin-boundary break: the policy guarantee the pool admin deployed the extension to enforce is entirely defeated, and LP funds are exposed to trades from counterparties the pool was explicitly designed to exclude.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery swap entry point. Any pool admin who wants their allowlisted users to have a normal swap experience must allowlist the router. The bypass requires no special privileges, no flash loans, and no multi-step setup: a single `exactInputSingle` call from any EOA is sufficient. The condition (router allowlisted on a curated pool) is the expected production configuration, not an edge case.

## Recommendation
The extension must gate the originating user, not the direct pool caller. The minimal safe fix is to check `recipient` instead of `sender` in `beforeSwap`, since `recipient` is already passed to the extension as the second argument and cannot be spoofed by the router. Alternatively, the router can encode `msg.sender` into `extensionData` and the extension can decode and check that address, though this requires trusting the router to supply the correct value.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in beforeSwap slot
  alice = allowlisted swapper (allowedSwapper[pool][alice] = true)
  router = MetricOmmSimpleRouter
  pool admin calls setAllowedToSwap(pool, router, true)
    → required so alice can use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(bob, true, X, ...) [msg.sender = router]
        → _beforeSwap(router, bob, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives output tokens

Result:
  Bob, a non-allowlisted address, successfully swaps on a curated pool.
  The allowlist is fully bypassed.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
