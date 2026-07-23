Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, making the allowlist universally bypassable or broken for all router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument passed from the pool, which is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is the caller, `sender` resolves to the router address, not the end-user. This creates an irreconcilable dilemma: allowlisting the router nullifies the allowlist for all users, while not allowlisting it breaks the standard periphery swap path for all allowlisted users.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient, ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The router is `msg.sender` of this call. The pool therefore passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` (all hops through `_exactOutputIterateCallback`).

**Two failure modes:**

| Admin configuration | Outcome |
|---|---|
| Router **not** allowlisted | Every allowlisted user who routes through the router gets `NotAllowedToSwap`. Standard periphery path broken. |
| Router **allowlisted** | Every non-allowlisted user bypasses the allowlist via `exactInputSingle`/`exactInput`/`exactOutput`. Allowlist nullified. |

No existing guard in `SwapAllowlistExtension`, `ExtensionCalling`, or `MetricOmmPool` propagates the original `msg.sender` (the end-user) through to the extension.

## Impact Explanation
**Allowlist bypass (router allowlisted):** Any non-allowlisted address can trade on a curated pool by routing through `MetricOmmSimpleRouter`. The pool admin's intent to restrict trading to specific counterparties is completely defeated. For institutional-only or compliance-gated pools, this allows arbitrary users to extract value at oracle-anchored prices meant only for approved parties — a direct admin-boundary break by an unprivileged path.

**Broken core swap path (router not allowlisted):** Allowlisted users who rely on the standard periphery router — the primary user-facing swap interface — cannot execute swaps. This renders the pool's swap functionality unusable through the supported periphery path, constituting broken core pool functionality.

## Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` and expects users to interact through `MetricOmmSimpleRouter` is affected. The router is the documented, primary swap interface. The bypass requires only a standard `exactInputSingle` call with no special privileges, no tokens pre-positioned, and no admin cooperation. One of the two failure modes is inevitable for any pool using both the extension and the router.

## Recommendation
The extension must receive the actual end-user identity, not the intermediary's address. Options:

1. **Pass original `msg.sender` through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it. Requires a trusted encoding convention between router and extension.
2. **Dedicated `originalSender` parameter:** Add an `originalSender` field to the `beforeSwap` hook interface, populated by the pool from a trusted transient-storage slot set by the router at entry (analogous to how the router already stores the payer via `_setNextCallbackContext`).
3. **Check `recipient` instead of `sender`:** If the intent is to gate who receives output tokens, `recipient` is the correct field. However, for input-side gating, neither field alone is sufficient when a router is involved.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-based swaps.
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite not being on the allowlist.

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
