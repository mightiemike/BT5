### Title
`SwapAllowlistExtension` sender identity is the router, not the user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. For any router-mediated swap to work on an allowlisted pool, the pool admin must allowlist the router address. Once the router is allowlisted, every user — including those explicitly excluded — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- always the direct caller of swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `msg.sender` is the router, so the extension receives `sender = router`. The extension then checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Two cases arise:**

**Case 1 — Router not allowlisted:** All router-mediated swaps revert with `NotAllowedToSwap`, even for users who are individually allowlisted. Allowed users cannot use the router at all.

**Case 2 — Router allowlisted (necessary for router-mediated swaps to work):** The extension passes for every caller of the router, including users who are explicitly excluded from the allowlist. The per-user gate is completely bypassed.

The same identity mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with the router as `msg.sender`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. A disallowed user can execute swaps against the pool's liquidity at oracle prices, extracting value that the pool admin intended to reserve for allowlisted participants. This is a direct loss of the pool's access-control invariant with fund-impacting consequences: unauthorized users trade at oracle-fair prices against LP capital that was meant to be protected.

---

### Likelihood Explanation

The likelihood is medium. It requires: (1) a pool deployed with `SwapAllowlistExtension` as a configured `beforeSwap` hook, and (2) the pool admin allowlisting the router (which is the only way to let allowlisted users use the router). Both conditions are the natural, expected production configuration for any curated pool that also wants to support the standard periphery router. The `MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the periphery, so the attack path is reachable by any user who reads the contract.

---

### Recommendation

The `sender` forwarded to extensions must represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-side**: Have the router pass the actual user address as an additional field in `extensionData` so that the extension can decode and check it. This requires a convention between the router and the extension.

2. **Extension-side (preferred)**: Redesign `SwapAllowlistExtension` to check the `recipient` or a user-supplied address decoded from `extensionData` rather than the raw `sender` when the `sender` is a known router. Alternatively, the pool interface could be extended to carry an explicit `originator` field alongside `sender`.

The cleanest fix is to have `MetricOmmSimpleRouter` encode `msg.sender` (the actual user) into `extensionData` and have `SwapAllowlistExtension` decode and check that address when present, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin allowlists `allowedUser` via setAllowedToSwap(pool, allowedUser, true)
  - Pool admin allowlists `router` via setAllowedToSwap(pool, router, true)
    (required so allowedUser can use the router)

Attack:
  - `disallowedUser` calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true (router is allowlisted)
  - Swap executes for disallowedUser at oracle price
  - disallowedUser receives output tokens; allowlist policy is bypassed
```

**Call path:**
```
disallowedUser → MetricOmmSimpleRouter.exactInputSingle()
    → pool.swap(recipient, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
            → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                → allowedSwapper[pool][router] == true → PASSES
        → swap executes
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
