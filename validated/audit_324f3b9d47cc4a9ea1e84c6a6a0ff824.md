Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Real User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which `MetricOmmPool.swap()` populates with its own `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` is the router contract, not the end user. Any non-allowlisted address can therefore bypass a curated pool's per-user swap allowlist by routing through the public router, because the extension only ever sees the router address and never the real initiator.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` unchanged into every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed — the router address when the call originates from `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without encoding the real user's address anywhere the extension can read: [4](#0-3) 

This creates a binary broken state. If the router is **not** allowlisted, all router-mediated swaps revert even for legitimately allowlisted users. If the router **is** allowlisted (the only way to enable router-mediated swaps for legitimate users), every address on the network can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`, because `allowedSwapper[pool][router]` evaluates to `true` for all callers.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` gates on the explicit `owner` argument (supplied by the caller, not derived from `msg.sender`), so it is not affected: [5](#0-4) 

## Impact Explanation

A pool admin who deploys a curated pool (e.g., for KYC'd traders or whitelisted market makers) and configures `SwapAllowlistExtension` with a per-user allowlist cannot enforce that allowlist for any user who routes through `MetricOmmSimpleRouter`. Any non-allowlisted address can trade on the restricted pool, receiving oracle-priced output tokens while the LP position absorbs the trade. This is a complete, direct bypass of the configured access-control guard — broken core pool functionality causing potential loss of LP principal to unauthorized counterparties.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract. No special privilege, token balance, or setup is required beyond holding the input token. Any user who observes that a pool has a swap allowlist can immediately bypass it in a single transaction with zero preconditions. The bypass is repeatable and requires no privileged cooperation.

## Recommendation

The extension must gate on the economically relevant actor — the end user — not the intermediary router. Viable approaches:

1. **Encode the real initiator in `extensionData`**: Have `MetricOmmSimpleRouter` ABI-encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. Pair this with a trusted-router registry in the extension so it only accepts this encoding from known routers; for direct callers, fall back to checking `sender` directly.
2. **Align with the deposit allowlist pattern**: Add an explicit `swapper` identity argument to `pool.swap()` (analogous to `owner` in `addLiquidity`) so the router can forward the real user address as a first-class parameter rather than relying on `msg.sender`.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     (required to allow any router-mediated swaps for legitimate users).
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. attacker (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: curated_pool,
           tokenIn: token0,
           zeroForOne: true,
           amountIn: X,
           amountOutMinimum: 0,
           ...
       })
  5. Router calls pool.swap(recipient, ...) — msg.sender = router.
  6. pool._beforeSwap(sender=router, ...) is dispatched.
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  8. Swap executes successfully for the non-allowlisted attacker.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; attacker receives oracle-priced output tokens.
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
