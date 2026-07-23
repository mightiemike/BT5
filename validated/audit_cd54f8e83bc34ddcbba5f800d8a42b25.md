Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the end-user. If the pool admin allowlists the router (required for any user to swap through it), every non-allowlisted user can bypass the per-user restriction by routing through the router.

## Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the first parameter forwarded by the pool. `MetricOmmPool.swap` always passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol lines 230-240
_beforeSwap(
    msg.sender,   // <-- becomes `sender` in the extension
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly with `msg.sender = router`:

```solidity
// MetricOmmSimpleRouter.sol lines 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same mismatch exists in `exactOutputSingle`, `exactInput`, `exactOutput`, and the recursive `_exactOutputIterateCallback` path (line 220-228 of `MetricOmmSimpleRouter.sol`).

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the actual position beneficiary), not `sender` (the direct caller of `pool.addLiquidity()`):

```solidity
// DepositAllowlistExtension.sol line 38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The swap extension has no equivalent "real beneficiary" parameter — `recipient` is the output receiver, not the economic actor initiating the trade.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses loses that restriction entirely for any user routing through `MetricOmmSimpleRouter`. The pool admin is forced into a binary choice: (1) do not allowlist the router, blocking all router-mediated swaps including for permitted users; or (2) allowlist the router, opening swaps to every user regardless of their individual allowlist status. There is no configuration that allows selective per-user enforcement through the router. Any non-allowlisted user can execute swaps against a restricted pool, receiving output tokens at oracle-anchored prices — a direct bypass of a core access-control invariant with fund-impacting consequences (unauthorized parties drain pool liquidity at oracle prices).

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery swap path. Pool admins who deploy `SwapAllowlistExtension` to restrict access will naturally also allowlist the router so their permitted users can trade conveniently. The moment the router is allowlisted, the bypass is open to everyone. No special privileges, flash loans, or unusual token behavior are required — a single `exactInputSingle` call suffices. The bypass is repeatable and unconditional.

## Recommendation

The `beforeSwap` hook must gate on the actual end-user identity, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user via `extensionData`**: Require callers (including the router) to encode the originating user address in `extensionData`, and verify it in the extension. The router would need to be updated to forward `msg.sender` in `extensionData` for each hop.

2. **Redesign the hook signature**: Add an explicit `originator` field to `beforeSwap` that the pool populates from a trusted source (e.g., transient context set by the router before calling `pool.swap()`), analogous to how `DepositAllowlistExtension` uses `owner` rather than `sender`.

## Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is permitted
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so alice can use it
  - bob is NOT in the allowlist

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ..., recipient: bob})
  2. Router calls pool.swap(bob, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes; bob receives output tokens
  6. SwapAllowlistExtension never checked bob's address
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
