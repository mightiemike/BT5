Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Allowing Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks whether the router is allowlisted — not the originating user. Any pool admin who allowlists the router to enable legitimate users to access the pool via the standard periphery interface inadvertently grants every address on-chain unrestricted swap access, completely defeating the curation gate.

## Finding Description

**Root cause — three-step call chain, all confirmed in production code:**

**Step 1:** `MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap(); the router when routing
  recipient, zeroForOne, amountSpecified, priceLimitX64,
  packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
``` [1](#0-0) 

**Step 2:** `ExtensionCalling._beforeSwap` forwards `sender` unchanged into every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
``` [2](#0-1) 

**Step 3:** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is whoever called `pool.swap()`:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

**Step 4:** `MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly as `msg.sender = router`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The originating user's address (`msg.sender` inside the router) is stored only in transient callback context for payment purposes and is never forwarded to the pool or extension. There is no existing guard that recovers the original user identity in `beforeSwap`.

**Impossible choice for pool admin:**
- Do **not** allowlist the router → allowlisted users cannot use the standard periphery interface; they must call the pool directly.
- **Allowlist the router** → `allowedSwapper[pool][router] == true` passes for every caller regardless of their own allowlist status, completely defeating the curation gate.

## Impact Explanation

A pool admin who deploys a curated pool (e.g., for KYC'd counterparties, institutional LPs, or regulatory compliance) and allowlists the router so that legitimate users can access the pool via the standard periphery interface inadvertently opens the pool to every address on-chain. Non-allowlisted users can swap at the oracle-derived bid/ask prices the pool was designed to offer only to specific parties. This constitutes a direct loss of LP principal (unauthorized parties extract value at privileged prices) and a complete bypass of the pool's access-control policy — a broken core pool invariant meeting the "admin-boundary break by an unprivileged path" and "broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants allowlisted users to use the standard router must allowlist the router address — this is the natural, expected operational path. Once the router is allowlisted, the bypass is trivially reachable by any EOA with no special privileges, no capital requirements beyond the swap amount, and is repeatable indefinitely.

## Recommendation

The extension must check the **originating user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user through the router via `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, while also verifying `msg.sender` (the pool) is a known pool to preserve the `onlyPool` guard. This is the cleanest fix.

2. **Document and enforce incompatibility:** Mark `SwapAllowlistExtension` as incompatible with router-mediated swaps and enforce this at the factory/deployment level (e.g., reject pool configurations that pair `SwapAllowlistExtension` with a known router allowlist entry).

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, router allowlisted, attacker NOT allowlisted
address pool = ...; // has SwapAllowlistExtension configured
address router = address(metricOmmSimpleRouter);

// Pool admin allowlists the router so legitimate users can use it
vm.prank(poolAdmin);
swapAllowlistExtension.setAllowedToSwap(pool, router, true);

// Attacker is NOT allowlisted
assertFalse(swapAllowlistExtension.isAllowedToSwap(pool, attacker));

// Attacker bypasses the allowlist by going through the router
vm.prank(attacker);
token0.approve(address(router), type(uint256).max);
// Succeeds — extension sees sender=router, which is allowlisted
metricOmmSimpleRouter.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Attacker receives token1 from a pool they were never authorized to access
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
