### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the **router contract**, not the actual end-user. If the pool admin allowlists the router address (the natural step to enable router-based swaps for approved users), every user — including non-allowlisted ones — can bypass the swap allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is used, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

The pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`. The actual end-user's address is stored only in transient storage for the payment callback and is never surfaced to the extension.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` evaluates to `allowedSwapper[pool][router] = true` for **every** user who routes through the router, regardless of their individual allowlist status.

This is structurally identical to the external report's OR-condition bug: just as USDC is always present in valid spot markets and causes the OR check to pass for any market, the router address is always the `sender` for router-mediated swaps and causes the allowlist check to pass for any user once the router is allowlisted.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or approved counterparties loses its access control entirely for router-mediated swaps. Any unpermissioned user can execute swaps against the pool's liquidity at oracle-derived prices, draining LP value through arbitrage or directional trading that the allowlist was designed to prevent. This is a direct loss of LP principal and a broken core pool invariant (curated access).

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router — a natural and expected operational step for any curated pool that intends to support the standard periphery. The `MetricOmmSimpleRouter` is the canonical user-facing entry point documented in the protocol. An admin who allowlists individual users and also allowlists the router (to let those users trade via the router) will unknowingly open the pool to all users. There is no warning in the extension, the router, or the documentation that allowlisting the router collapses per-user identity to a single shared address.

---

### Recommendation

The extension must verify the actual end-user, not the intermediary. Two approaches:

1. **Pass the real user through `extensionData`**: Require the router to encode `msg.sender` (the actual user) into `extensionData`, and have the extension decode and verify it. This requires a coordinated change to the router and the extension.

2. **Check `sender` against the allowlist only when `sender` is not a known router; otherwise decode the real user from `extensionData`**: More complex but backward-compatible.

The simplest correct fix is to have the router encode the originating user in `extensionData` and have `SwapAllowlistExtension` decode and check that address when the direct `sender` is a recognized router:

```solidity
// In SwapAllowlistExtension.beforeSwap:
address effectiveSender = sender;
if (isKnownRouter[sender] && extensionData.length >= 20) {
    effectiveSender = abi.decode(extensionData, (address));
}
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][effectiveSender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Alternatively, redesign the extension to check `sender` only for direct pool calls and require router-mediated swaps to carry a signed user identity.

---

### Proof of Concept

**Setup:**
- Deploy a pool with `SwapAllowlistExtension` configured.
- Admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only approved swapper.
- Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.

**Attack:**
- Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
- Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` with `msg.sender = router`.
- Pool calls `_beforeSwap(router, recipient, ...)`.
- Extension evaluates `allowedSwapper[pool][router] = true` → passes.
- Bob's swap executes at oracle price against LP liquidity.

**Direct call (control):**
- Bob calls `pool.swap(...)` directly.
- Extension evaluates `allowedSwapper[pool][bob] = false` → reverts with `NotAllowedToSwap`.

The bypass is reachable through the canonical `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` entry points of `MetricOmmSimpleRouter`. [1](#0-0) [2](#0-1) [3](#0-2)

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
