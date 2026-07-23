### Title
SwapAllowlistExtension Gates the Router Address Instead of the Original User, Allowing Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. If the pool admin allowlists the router address (a natural step to enable router-mediated swaps for their curated pool), every unprivileged user can bypass the individual allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap(), not the original user
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the first argument (the direct caller of `pool.swap()`). When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

The router does not forward the original `msg.sender` (the user) to the pool. The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

A pool admin who wants to support router-mediated swaps for their curated pool will naturally allowlist the router address. Once the router is allowlisted, every user — including those explicitly not on the allowlist — can bypass the gate by calling any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

The same mismatch applies to the multi-hop `exactOutput` path, where the router calls subsequent pools from inside `_exactOutputIterateCallback`, again with `msg.sender = router`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on the `owner` argument (the position owner), which the pool passes through unchanged regardless of who calls `addLiquidity`. The swap extension does not have an equivalent owner-style argument; it relies solely on `sender`, which collapses to the router address on any router-mediated path.

### Impact Explanation

Any user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter` whenever the router address is allowlisted. The pool admin's intent — restricting swaps to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market participants) — is completely defeated. Non-allowlisted users can execute swaps at oracle-anchored prices, draining LP liquidity at the pool's bid/ask spread, which constitutes a direct loss of LP principal and protocol fees on a pool that was explicitly configured to prevent open access.

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is a natural and expected administrative action: a pool admin who deploys a curated pool and wants their allowlisted users to be able to use the standard periphery router will add the router to `allowedSwapper`. The admin's mental model is that the router is a trusted intermediary that will pass through user identity; the actual behavior is that the router becomes a universal bypass key. The router is a public, permissionless contract, so once it is allowlisted, any user can exploit the bypass without any further privileged action.

### Recommendation

The `SwapAllowlistExtension` should gate on the economically relevant actor — the original user — not the direct caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` as a verified `sender` field in `extensionData` or via a dedicated parameter, and the extension should read that value after verifying it was set by a trusted router.

2. **Alternatively, check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the user, but this is not reliable for multi-hop paths.

The cleanest fix is to mirror the `DepositAllowlistExtension` pattern: introduce a per-position `owner`-equivalent for swaps (e.g., a signed or router-attested user address in `extensionData`) and gate on that value rather than on the raw `sender`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted to support periphery

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  →  no revert
  - bob's swap executes at oracle price, draining LP liquidity

Direct call check (for comparison):
  - bob calls pool.swap(...) directly with msg.sender = bob
  - Extension checks allowedSwapper[pool][bob] == false  →  NotAllowedToSwap revert
```

The bypass is reachable through all four router entry points: `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
