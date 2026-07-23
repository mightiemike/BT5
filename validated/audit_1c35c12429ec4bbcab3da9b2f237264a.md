Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` as seen by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool receives the router as `msg.sender` and passes it as `sender` to the extension. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users simultaneously opens the pool to all unprivileged users via the same router path.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `sender` is the router when the user enters through `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — the pool sees `msg.sender = router`, not the end user: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the pool receives the router as `msg.sender`. A pool admin who allowlists individual users but also allowlists the router (to let those users use the standard UI) inadvertently grants swap access to every unprivileged address. The admin has no on-chain signal that this is the consequence.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` (the payer/intermediary) and checks `owner` (the economic beneficiary): [5](#0-4) 

`SwapAllowlistExtension` does not apply this same pattern, leaving the swap guard bound to the wrong actor.

## Impact Explanation
A curated pool (KYC-gated, institution-only, or rate-limited) configured with `SwapAllowlistExtension` can be freely traded against by any unprivileged address once the router is allowlisted. The pool's LP assets are exposed to swaps from actors the pool admin explicitly intended to exclude. This is a broken core pool functionality and an admin-boundary break: the configured access guard is fully bypassed through the canonical supported periphery path. Severity: **High**.

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router. This is a natural, expected action — the router is the canonical swap entrypoint, and any admin who wants allowlisted users to use the standard UI will add it. The admin receives no warning that doing so opens the pool to everyone. No special attacker capability is required beyond calling the public router. Likelihood: **Medium-High**.

## Recommendation
The extension must check the economically relevant actor — the end user — not the intermediary. Two approaches:

1. **Check `recipient` instead of `sender`:** For swap allowlists the recipient is the economic beneficiary. Update `SwapAllowlistExtension.beforeSwap` to check `allowedSwapper[msg.sender][recipient]`. This mirrors the `DepositAllowlistExtension` pattern of ignoring the intermediary and checking the economic owner.

2. **Pass the original user via `extensionData`:** Have `MetricOmmSimpleRouter` encode the actual `msg.sender` (the user) into `extensionData`, and update `SwapAllowlistExtension` to decode and check that field when `sender` is a known router. This requires a trust assumption on the router.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted to support alice's UI usage
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(router, bob, ...) → extension receives sender = router
  4. Extension checks allowedSwapper[pool][router] == true → PASSES
  5. bob's swap executes successfully despite allowedSwapper[pool][bob] == false

Result:
  - bob successfully swapped on a pool that was supposed to exclude him
  - The allowlist guard is completely bypassed via the supported periphery path
  - Any unprivileged user can repeat this attack
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
