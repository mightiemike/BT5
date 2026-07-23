### Title
`SwapAllowlistExtension` Gates on Router Address Instead of Actual User When Swaps Are Routed Through `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When any user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every non-allowlisted user can bypass the restriction by routing through the same router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool populates `sender` with its own `msg.sender` — the direct caller of `pool.swap()`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`, so the pool's `msg.sender` is the router contract:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants allowlisted users to be able to swap through the router must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, regardless of whether that caller is on the allowlist. The pool admin has no way to simultaneously permit router-mediated swaps for allowlisted users and block them for non-allowlisted users.

The same structural flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly, so all produce `sender = router`.

---

### Impact Explanation

The `SwapAllowlistExtension` access control is completely nullified for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can trade in pools that are intended to be restricted to specific counterparties (e.g., KYC-gated, institutional-only, or whitelist-only pools). The pool admin's intended access boundary is broken by an unprivileged, publicly reachable path.

---

### Likelihood Explanation

Likelihood is high:
- `MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol.
- Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants legitimate users to be able to use the router must allowlist the router address.
- Once the router is allowlisted, the bypass is trivially reachable by any EOA with no special privileges.

---

### Recommendation

The extension must gate on the **economic actor**, not the intermediary. Options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to encode honestly, which is acceptable for a protocol-owned router.
2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is often the economic beneficiary; however, this is also spoofable.
3. **Document the limitation clearly**: If the design intent is that the allowlist only applies to direct `pool.swap()` callers, document that router-mediated swaps are always open and do not deploy the extension on pools that require user-level gating.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` should be able to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to allow `alice` to use the router.
4. `bob` (not on the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The router calls `pool.swap(bob, ...)`.
6. The pool calls `_beforeSwap(sender=router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `bob`'s swap executes successfully in the restricted pool, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
