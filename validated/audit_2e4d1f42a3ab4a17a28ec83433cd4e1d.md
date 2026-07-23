Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those not individually allowlisted — can bypass the per-user gate by routing through the public router.

## Finding Description
`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the router the `msg.sender` of `pool.swap()`. [1](#0-0) 

`MetricOmmPool.swap` then calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`: [2](#0-1) 

Inside `SwapAllowlistExtension.beforeSwap`, the check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. Because the router must be allowlisted for any router-mediated swap to work on this pool, the check passes for every user who routes through the router, regardless of whether they are individually authorized.

There are no additional guards in the extension or pool that identify the true end user. The `extensionData` field is passed through but the extension does not decode or validate it.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to enforce KYC, regulatory, or protocol-specific access control loses that control entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized parties can execute swaps against LP positions in a pool explicitly configured to exclude them, constituting broken core pool functionality and a direct policy bypass with fund-impacting consequences (unauthorized actors can drain arbitrage value or execute swaps against LP positions).

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap entrypoint. The only precondition is that the pool admin has allowlisted the router address — which is the only way to make router-mediated swaps work at all on an allowlisted pool, making this a near-certain operational configuration. Any non-allowlisted user who discovers the curated pool can trivially exploit this without any privileged action.

## Recommendation
The extension must identify the true end user, not the intermediary. Two viable approaches:

1. **Pass the original caller in `extensionData`**: Have the router encode `msg.sender` (the end user) into `extensionData` and have the extension decode and check that address when `sender` is a recognized trusted router.
2. **Trusted-forwarder registry**: The extension maintains a mapping of trusted routers; when `sender` is a trusted router, require the actual user identity to be supplied and verified via `extensionData`.
3. **Do not allowlist the router**: Require all allowlisted users to call `pool.swap()` directly and document this restriction explicitly. This removes router usability for curated pools but eliminates the bypass.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension on beforeSwap hook
  - setAllowedToSwap(pool, router, true)       // enable router-mediated swaps
  - setAllowedToSwap(pool, userA, true)        // only userA is individually allowed
  - userB is NOT allowlisted

Attack:
  - userB calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes for userB despite userB not being allowlisted

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
