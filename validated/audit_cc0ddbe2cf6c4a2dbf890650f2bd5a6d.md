All four cited code locations are confirmed against the actual repository. The call chain is accurate:

- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the argument passed from the pool. [1](#0-0) 
- `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`. [2](#0-1) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension. [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` with no originator field; the router is `msg.sender` at the pool level. [4](#0-3) 

The bypass is real and unconditional once the router is allowlisted.

---

Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the router address when called via `MetricOmmSimpleRouter`. Any pool admin who allowlists the router to enable router-based swaps on a restricted pool inadvertently grants every user of that router unrestricted swap access, completely defeating the allowlist. No special knowledge or privilege is required beyond calling the public router.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`, passing the immediate caller as `sender`. When the call originates from `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the end-user. `ExtensionCalling._beforeSwap` encodes this value unchanged into the extension call. `SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]`. For router-based swaps to function on an allowlisted pool, the admin must add the router to the allowlist. Once added, the check passes for every caller of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` — regardless of whether that individual user is permitted. The router stores no per-user identity that the extension can inspect; the pool swap interface has no `originator` field.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set (e.g., KYC-verified traders, whitelisted market makers) is fully bypassed by any user routing through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps at oracle-derived prices against LP capital deposited under the assumption of a restricted counterparty set, causing LP losses and violating compliance or access-control invariants the pool admin relied upon. This constitutes broken core pool functionality (the allowlist guard) with direct fund-impact potential on LP capital.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, publicly deployed swap entry point. Any pool enabling router-based swaps must allowlist the router — this is the normal operational setup. No privileged access or special knowledge is required; any user can call the router. The bypass is unconditional once the router is allowlisted and requires no per-call setup.

## Recommendation
The extension must gate on the actual end-user, not the immediate caller of `pool.swap()`. The preferred fix is to pass an authenticated originator through `extensionData`: the router encodes `msg.sender` into `extensionData` before calling `pool.swap`, and `SwapAllowlistExtension.beforeSwap` decodes and verifies it. Alternatively, add an `originator` field to the pool swap interface so the router can forward the real user address and the extension can check `allowedSwapper[pool][originator]`. A short-term stopgap — rejecting calls where `sender` is a known router unless the admin explicitly opts in — is fragile and not recommended as a primary fix.

## Proof of Concept
```solidity
// Pool configured with SwapAllowlistExtension.
// Admin allowlists only trustedTrader and the router (required for router swaps).
allowlistExt.setAllowedToSwap(pool, address(router), true);
allowlistExt.setAllowedToSwap(pool, trustedTrader, true);

// Attacker (not allowlisted) bypasses the guard via the router:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             pool,
        tokenIn:          token0,
        recipient:        attacker,
        zeroForOne:       true,
        amountIn:         1_000e6,
        amountOutMinimum: 0,
        priceLimitX64:    0,
        deadline:         block.timestamp,
        extensionData:    ""
    })
);
// Succeeds: pool.swap sees sender=router, extension checks allowedSwapper[pool][router]=true.
// Attacker swaps against restricted LP capital without being on the allowlist.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
