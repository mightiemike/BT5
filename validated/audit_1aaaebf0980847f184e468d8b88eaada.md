Audit Report

## Title
`SwapAllowlistExtension` allowlist bypassed when router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on `sender`, which is the direct `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router contract address, not the originating user. A pool admin who adds the router to the allowlist to enable router-mediated swaps for their vetted users inadvertently grants every user on the network the ability to bypass the allowlist entirely.

## Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`sender` is populated by `MetricOmmPool.swap` as `msg.sender` of the `swap()` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,  // <-- direct caller of pool.swap()
    ...
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
``` [3](#0-2) 

So the extension sees `sender = address(router)`, not the originating user. The allowlist check becomes `allowedSwapper[pool][router]`.

**The trap:** A pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper`. The moment they do, `allowedSwapper[pool][router] == true` passes the check for **every** user who routes through the router — the allowlist is completely neutralised for that entry path.

The `DepositAllowlistExtension` does not share this flaw because it gates `owner` (the position beneficiary), which is passed explicitly and is not overwritten by the intermediary: [4](#0-3) 

## Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted counterparties is fully bypassed. Any unprivileged user can execute swaps against the pool's liquidity by routing through `MetricOmmSimpleRouter`, receiving output tokens at oracle-anchored prices. LP funds are consumed by unauthorized traders, and the pool's curation invariant is broken. This constitutes broken core pool functionality causing direct loss of LP assets — a High severity impact under Sherlock thresholds.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical periphery swap path; users are expected to use it.
- A pool admin who deploys `SwapAllowlistExtension` and wants their allowlisted users to access the router **must** add the router to the allowlist — there is no other mechanism.
- Once the router is added, the bypass is unconditional and requires no special privileges, timing, or state manipulation: any user calls `exactInputSingle` on the allowlisted pool.
- The admin has no way to simultaneously allow router usage and enforce per-user identity checks with the current extension design.

## Recommendation

Pass the originating user through the swap path rather than the immediate `msg.sender`. Two concrete options:

1. **Extend the extension interface** to carry an `originator` field (set by the pool to `tx.origin` or supplied by the router as part of `extensionData`) so the allowlist can gate the economic actor rather than the intermediary.
2. **Check `extensionData` in the router** — have the router ABI-encode the real user address into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it, combined with a `msg.sender`-is-trusted-router guard to prevent spoofing.

The simplest safe fix consistent with the existing architecture is option 2: the router encodes `msg.sender` (the user) into `extensionData`; the extension decodes it and checks `allowedSwapper[pool][decodedUser]` only when `sender` (the pool-visible caller) is a known trusted router.

## Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice] = true   // alice is the only intended swapper
  allowedSwapper[P][router] = true  // admin adds router so alice can use it

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: P,
      zeroForOne: true,
      amountIn: X,
      recipient: bob,
      ...
  })

  Router calls P.swap(bob, true, X, ..., "")
    msg.sender to pool = router
    pool calls _beforeSwap(sender=router, ...)
    extension checks allowedSwapper[P][router] == true  ✓
    swap executes, bob receives output tokens

Result:
  bob, a non-allowlisted user, successfully swaps against the curated pool.
  The allowlist guard is silently bypassed.
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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
