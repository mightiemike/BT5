Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`, but `sender` is sourced from `msg.sender` at the pool level — which is the router contract, not the original user — when a swap is routed through `MetricOmmSimpleRouter`. Any pool admin who allowlists the router address (the natural action to enable router-mediated swaps for their allowlisted users) inadvertently grants unrestricted swap access to every user who routes through it, rendering the allowlist ineffective.

## Finding Description
**Confirmed call chain:**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly. [1](#0-0) 

The router is therefore `msg.sender` at the pool level. `MetricOmmPool.swap` then calls `_beforeSwap(msg.sender, ...)`, passing the router address as `sender`. [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes that `sender` value (= router address) and dispatches it to every configured extension via `_callExtensionsInOrder`. [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then evaluates:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` = pool (correct) and `sender` = router address (wrong — should be the original user). [4](#0-3) 

When a pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for their allowlisted users, the check `allowedSwapper[pool][router]` passes for **every** user who routes through the router, regardless of whether that individual user is allowlisted. The extension has no mechanism to recover the original caller's address from the `sender` argument it receives.

## Impact Explanation
Any user — including those explicitly excluded from the allowlist — can trade on a restricted curated pool by routing through `MetricOmmSimpleRouter`. The allowlist is the sole access-control mechanism for swap gating on such pools. Once bypassed, disallowed users can execute swaps, drain liquidity at oracle prices, and extract value from pools intended to be private or KYC-gated. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where an unprivileged path circumvents the pool admin's access control.

## Likelihood Explanation
The bypass requires only that the pool admin has allowlisted the router address — the natural and expected operational step for any pool that wants to support both direct and router-mediated swaps. The router is a first-party, publicly deployed periphery contract, so allowlisting it is routine. Once done, the bypass is available to any unprivileged user with no special setup, no privileged access, and no additional preconditions.

## Recommendation
`SwapAllowlistExtension.beforeSwap` must gate on the original user, not the immediate pool caller. The preferred approach is for the router to encode `msg.sender` (the original user) into `extensionData` under a standardized field (e.g., a `swapper` field), and for the extension to decode and verify it — but only after confirming that the pool's `msg.sender` is a trusted router. Alternatively, the extension can check `recipient` for single-hop swaps, though this breaks for multi-hop paths. The root fix is ensuring the extension receives and verifies the identity of the actual economic actor, not the intermediary contract.

## Proof of Concept
```solidity
// Pool P has SwapAllowlistExtension configured.
// Pool admin allowlists the router so that allowlisted users can use it:
swapAllowlist.setAllowedToSwap(P, address(router), true);

// Alice is NOT individually allowlisted:
// allowedSwapper[P][alice] == false

// Alice bypasses the allowlist by routing through the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: P,
    recipient: alice,
    zeroForOne: true,
    amountIn: 1e18,
    ...
}));
// MetricOmmPool.swap: _beforeSwap(msg.sender=router, ...)
// ExtensionCalling: encodes sender=router, calls SwapAllowlistExtension
// SwapAllowlistExtension: allowedSwapper[P][router] == true → passes
// Alice's swap executes on the restricted pool.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
