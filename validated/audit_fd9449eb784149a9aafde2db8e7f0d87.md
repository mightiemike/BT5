Audit Report

## Title
`SwapAllowlistExtension` Allowlist Fully Bypassed via Router When Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap()` call — the immediate caller, not the end user. When `MetricOmmSimpleRouter` is allowlisted to support router-mediated swaps, every non-allowlisted user can bypass the restriction by routing through the router, because the extension sees `sender = router` and `allowedSwapper[pool][router] == true`.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no forwarding of the original `msg.sender`: [4](#0-3) 

This makes `msg.sender` inside `pool.swap()` equal to the router address, so the extension receives `sender = router`. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` entry points.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner argument), which is explicitly passed by the caller and preserved through the call chain — not derived from `msg.sender`.

## Impact Explanation
A curated pool's swap allowlist is completely bypassed for any user who routes through the allowlisted `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps in pools designed to restrict access (e.g., KYC-gated, institutional, or regulatory-compliant pools). This is a direct admin-boundary break: the pool admin's access control policy is rendered ineffective for all router-mediated swaps, exposing LP depositors to counterparties the pool admin explicitly intended to exclude. Unauthorized users can extract value from the pool's liquidity at oracle-anchored prices.

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router address. This is a natural and expected operational step: any pool admin who wants their allowlisted users to use the standard periphery router must call `setAllowedToSwap(pool, router, true)`. The moment they do, the allowlist is fully open to all router users. The trigger is a valid, semi-trusted pool admin action that is not itself malicious — it is the predictable configuration for enabling router support. No special privileges or malicious setup are required from the attacker beyond calling the public router.

## Recommendation
The extension must gate on the end user's identity, not the intermediary's. The preferred fix is for the router to encode the original `msg.sender` in `extensionData`, and for `SwapAllowlistExtension.beforeSwap` to decode and verify that address when the caller is a known router. Alternatively, the extension can check `recipient` instead of `sender` (the address receiving output tokens, set by the user), though this changes the semantic of "who is swapping." A minimal mitigation is to add a check in `setAllowedToSwap` that rejects known router addresses, but this is fragile and does not scale.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router support
  - Pool admin does NOT allowlist attacker address

Attack:
  - attacker (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: pool,
          recipient: attacker,
          zeroForOne: true,
          amountIn: X,
          ...
      })

  - Router calls pool.swap(attacker, true, X, ..., extensionData)
    → msg.sender inside pool.swap() = router address
  - pool._beforeSwap(sender=router, recipient=attacker, ...)
  - SwapAllowlistExtension.beforeSwap(sender=router, ...) checks:
      allowedSwapper[pool][router] == true  ✓  → swap proceeds

Result:
  - Attacker swaps successfully despite not being on the allowlist
  - The allowlist policy is completely bypassed for all router-mediated swaps
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
