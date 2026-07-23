Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the originating EOA. Any pool admin who allowlists the router (the only way to let allowlisted users use the standard periphery path) inadvertently opens the pool to every caller who routes through the router, regardless of individual allowlist status.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making itself `msg.sender` to the pool: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. A pool admin who wants allowlisted users to use the standard periphery path must call `setAllowedToSwap(pool, router, true)`. Once that entry exists, the condition is satisfied for every caller who routes through the router, because `sender` is always the router address regardless of who initiated the transaction.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position beneficiary) rather than `sender` (the direct caller), avoiding this exact problem: [5](#0-4) 

## Impact Explanation

Any user — including those explicitly excluded from the allowlist — can swap on a curated pool by routing through `MetricOmmSimpleRouter`. The pool's LP positions are exposed to counterparties the pool admin intended to exclude. Depending on the pool's purpose (KYC-gated, institutional-only, front-running-resistant), this allows unauthorized extraction of value from LP capital at oracle-anchored prices, constituting a direct loss of LP assets and a broken core pool invariant (the allowlist guard fails open on the standard periphery path). This meets the "Broken core pool functionality causing loss of funds" and "Admin-boundary break bypassed by an unprivileged path" criteria.

## Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router — a routine and expected action for any pool that intends to support the standard periphery UX. No privileged access, special tokens, or malicious setup is needed by the attacker. Any EOA can call `MetricOmmSimpleRouter.exactInputSingle` with a valid pool address and a non-zero amount. The condition is met whenever at least one legitimate user is expected to use the router, making this highly likely in any real deployment.

## Recommendation

The extension must check the economically relevant actor, not the immediate caller of the pool. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Trusted-forwarder pattern**: The router appends the originating user to `extensionData` and the extension verifies the router's identity before trusting the appended address, similar to how `DepositAllowlistExtension` uses `owner` instead of `sender`.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured in beforeSwapOrder
  admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
  admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool:       pool,
      recipient:  bob,
      zeroForOne: true,
      amountIn:   X,
      ...
    })

  Execution path:
    router.exactInputSingle
      → pool.swap(recipient=bob, ...)   // msg.sender to pool = router
      → _beforeSwap(sender=router, ...)
      → SwapAllowlistExtension.beforeSwap(sender=router, ...)
      → check: allowedSwapper[pool][router] == true  ✓  (admin set this for alice's benefit)
      → swap executes for bob without revert

Result:
  bob swaps on a pool he is not individually allowlisted for.
  The allowlist guard is completely bypassed for any caller using the router.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
