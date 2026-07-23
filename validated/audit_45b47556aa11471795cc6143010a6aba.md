The code confirms all three legs of the claim. Let me verify the exact lines before rendering the final output.

- `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()` — confirmed at lines 230–231.
- `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — confirmed at line 37.
- `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly with no encoding of the original user into `extensionData` — confirmed at lines 72–80.

The two-case analysis holds exactly as described. The finding is valid.

---

Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of user address, allowing any caller to bypass per-user swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` always sets to its own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the only way for any allowlisted user to use the router), every caller of the router — including explicitly excluded addresses — can execute swaps against the pool, completely bypassing the per-user access control.

## Finding Description

`MetricOmmPool.swap()` unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` against the per-pool allowlist, where `msg.sender` is the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly without encoding the original `msg.sender` into `extensionData`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // user-supplied, not the user's address
  );
``` [3](#0-2) 

The same pattern applies to `exactInput` (L104–112), `exactOutputSingle` (L136–137), and `exactOutput` (L165–181) — all router entry points call `pool.swap()` with the router as `msg.sender`. [4](#0-3) 

**Two cases arise:**

- **Case 1 — Router not allowlisted:** All router-mediated swaps revert with `NotAllowedToSwap`, even for individually allowlisted users. Allowlisted users cannot use the router at all.
- **Case 2 — Router allowlisted (required for Case 1 to be avoided):** The extension passes for every caller of the router, including addresses explicitly excluded from the allowlist. The per-user gate is completely bypassed.

No existing guard in `SwapAllowlistExtension` or `MetricOmmSimpleRouter` recovers the original user identity. The `extensionData` field is passed through as-is from the user's call parameters and is not authenticated.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. A disallowed user can execute swaps against the pool's liquidity at oracle-fair prices, extracting value that the pool admin intended to reserve for allowlisted participants. This is a direct, fund-impacting violation of the pool's access-control invariant: unauthorized users trade against LP capital that was meant to be protected, constituting a broken core pool functionality with direct loss consequences for LPs and the pool's access policy.

## Likelihood Explanation
The likelihood is medium. It requires: (1) a pool deployed with `SwapAllowlistExtension` as a configured `beforeSwap` hook, and (2) the pool admin allowlisting the router — which is the only way to let allowlisted users use the router at all. Both conditions are the natural, expected production configuration for any curated pool that also wants to support the standard periphery router. `MetricOmmSimpleRouter` is the primary public swap entry point in the periphery, so the attack path is reachable by any user who reads the contracts. The attack is repeatable with no special privileges.

## Recommendation
The `sender` forwarded to extensions must represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-side (preferred):** Have `MetricOmmSimpleRouter` encode `msg.sender` (the actual user) into `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap()` decode and check that address when present, falling back to `sender` for direct pool calls.

2. **Extension-side alternative:** Extend the pool interface to carry an explicit `originator` field alongside `sender`, populated by the router with the original user address, and have `SwapAllowlistExtension` check `originator` instead of `sender`.

The cleanest fix is option 1: the router encodes `abi.encode(msg.sender)` into `extensionData` (or a dedicated field within it), and the extension decodes and verifies that address when `sender` is a known router.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin: setAllowedToSwap(pool, allowedUser, true)
  - Pool admin: setAllowedToSwap(pool, router, true)
    (required so allowedUser can use the router at all)

Attack:
  - disallowedUser calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes for disallowedUser at oracle price
  - disallowedUser receives output tokens; allowlist policy is bypassed

Call path:
disallowedUser → MetricOmmSimpleRouter.exactInputSingle()
    → pool.swap(recipient, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
            → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                → allowedSwapper[pool][router] == true → PASSES
        → swap executes
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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
