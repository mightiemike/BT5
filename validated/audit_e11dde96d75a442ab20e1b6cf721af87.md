Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router contract is `msg.sender` of `pool.swap()`, so the allowlist gates the router address rather than the end user. If the router is allowlisted (the natural operational choice for a pool that supports routing), every user who routes through it bypasses the allowlist entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct map key) and `sender` is whoever called `pool.swap()`. Every `MetricOmmSimpleRouter` entry point calls `pool.swap()` directly, making the router the `sender`:

- `exactInputSingle` — L72-80
- `exactInput` — L104-112
- `exactOutputSingle` — L136-137
- `exactOutput` — L165-181
- `_exactOutputIterateCallback` — L220-228

The allowlist lookup therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Existing guards (`allowAllSwappers` and `allowedSwapper`) are both keyed on `sender`, so neither catches the actual end user when routing is involved.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to enforce KYC, institutional-only, or whitelist-only access is fully open to any public user who routes through `MetricOmmSimpleRouter`. The unauthorized user executes swaps at oracle-anchored prices against LP capital deposited under the assumption that only vetted counterparties would trade. This is a direct loss of LP principal through unauthorized price-taking and a complete failure of the pool's core access-control invariant — matching the "admin-boundary break bypassed by an unprivileged path" and "broken core pool functionality causing loss of funds" impact categories.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard public periphery entry point. A pool admin who configures an allowlist and also wants to support routing has no choice but to allowlist the router, which immediately opens the bypass. The attacker requires no special privilege, no flash loan, and no multi-block setup — a single call to any `exact*` function on the router suffices. The condition (router allowlisted + non-allowlisted user) is the expected operational state for any allowlisted pool that supports routing.

## Recommendation
The allowlist must gate the economic actor, not the intermediary. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before forwarding to the pool; the extension decodes and checks that address when present, falling back to `sender` for direct pool calls.
2. **Trusted-router registry with `tx.origin` fallback**: The extension maintains a registry of trusted routers; when `sender` is a trusted router, it checks `tx.origin` instead. This is safe in a read-only guard context with no reentrancy risk.

Approach (1) is cleaner long-term: the router explicitly encodes the originating user in `extensionData`, and the extension reads it when present.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  allowedSwapper[pool][router] = true   // admin allowlists router for routing support
  allowedSwapper[pool][alice]  = false  // alice is NOT on the allowlist

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)          // MetricOmmSimpleRouter.sol L72-80
    → pool calls _beforeSwap(msg.sender=router, ...)  // MetricOmmPool.sol L230-240
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true → passes   // SwapAllowlistExtension.sol L37-38
    → alice's swap executes against LP capital
    → NotAllowedToSwap is never raised
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
