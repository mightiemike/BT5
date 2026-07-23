Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` from the pool's perspective — i.e., whoever called `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router to enable the standard periphery flow, every user — including non-allowlisted ones — bypasses the guard entirely, as the extension cannot distinguish individual users arriving through the router.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to `_beforeSwap`.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

**Step 2 — `SwapAllowlistExtension.beforeSwap` checks that `sender` against the per-pool allowlist.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()`.

**Step 3 — `MetricOmmSimpleRouter` is always the entity that calls `pool.swap()`.**

`exactInputSingle` calls `pool.swap()` directly from the router, never forwarding the original `msg.sender` to the pool: [3](#0-2) 

Similarly for `exactInput`: [4](#0-3) 

And `exactOutputSingle`: [5](#0-4) 

**Consequence — irreconcilable conflict for any pool using both the allowlist extension and the router:**

| Admin configuration | Effect |
|---|---|
| Router NOT allowlisted | Allowlisted users cannot swap through the router — broken core functionality |
| Router IS allowlisted | `allowedSwapper[pool][router] = true` → every user passes the check — full allowlist bypass |

The `extensionData` bytes are user-controlled and ignored by `SwapAllowlistExtension`, so there is no existing mechanism to recover the true caller identity. [6](#0-5) 

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to specific users (KYC'd accounts, institutional traders, whitelisted counterparties) and also allowlists the router to support the standard periphery flow inadvertently opens the pool to all users. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()`, the extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and allows the swap. The allowlist guard is completely bypassed. Unauthorized users can trade on a curated pool, violating the pool's access policy — a direct, fund-impacting allowlist bypass matching the "Allowlist path" audit pivot: swap allowlist checks must not be bypassable through the router.

## Likelihood Explanation
The router is the primary user-facing swap interface. Pool admins who configure `SwapAllowlistExtension` to restrict access would naturally also want to support router-mediated swaps (the documented periphery path). Allowlisting the router is the only way to make the router work with the extension, and the admin has no indication that doing so grants all router users unrestricted access. The mismatch is not documented, and the extension's name implies it gates by swapper identity, which it cannot do through the router.

## Recommendation
- **Preferred**: Have the router encode `msg.sender` into `extensionData` before forwarding to the pool, and update `SwapAllowlistExtension.beforeSwap` to decode and check that value when present.
- **Alternative**: Document explicitly that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and must only be used with direct pool calls.
- **Structural fix**: Add a dedicated "actual payer" field to the swap hook arguments so extensions can always access the economic actor regardless of routing path.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Admin: setAllowedToSwap(pool, alice, true)   // allowlist Alice
3. Admin: setAllowedToSwap(pool, router, true)  // enable router path
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...)
       → pool.msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. SwapAllowlistExtension.beforeSwap(sender=router):
       allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes on the curated pool — allowlist fully bypassed.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
