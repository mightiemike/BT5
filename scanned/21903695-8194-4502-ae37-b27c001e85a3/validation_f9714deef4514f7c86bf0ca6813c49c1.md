### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Complete Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. A pool admin who allowlists the router (required for any router-mediated swap to work) inadvertently opens the gate to every user on the internet, completely defeating the curated-pool invariant.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as the address the pool recorded as `msg.sender` at the time `pool.swap()` was called:

```solidity
// MetricOmmPool.sol – swap()
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient, …
);
```

The extension then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, …, params.extensionData
);
```

So `sender` arriving at the extension is `address(router)`, not the end user. The extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

**The dilemma for the pool admin:**

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | ❌ Blocked (breaks UX) | ✓ Blocked |
| Yes | ✓ Passes | ❌ **Also passes — bypass** |

If the admin allowlists the router so that legitimate users can use the standard periphery path, every user on the internet can bypass the allowlist by routing through the same router.

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional-only, or whitelist-gated) relies on `SwapAllowlistExtension` to ensure only approved counterparties trade against its LP positions. When the bypass is active:

- Any unpermissioned user can execute swaps at oracle-anchored prices against LP capital that was deposited under the assumption of a restricted counterparty set.
- LPs suffer direct loss of principal: they receive the oracle-priced output token but the counterparty was never supposed to be allowed to trade.
- The entire purpose of the curated pool — protecting LPs from unvetted flow — is nullified.

This is a direct loss of LP principal caused by broken core pool access-control functionality, matching the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" impact gates.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical, publicly documented periphery entry point for swaps.
- Any user who wants to bypass the allowlist needs only to call `exactInputSingle` or `exactInput` on the router — no special privileges, no flash loans, no multi-step setup.
- Pool admins who deploy a curated pool and want to support the standard UX will allowlist the router, triggering the bypass automatically.
- Likelihood is **High**.

---

### Recommendation

The extension must gate the **original end user**, not the immediate caller of `pool.swap()`. Two complementary approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the extension to trust the router, so the router address must itself be verified (e.g., checked against the factory).

2. **Check `sender` only when `sender` is not a trusted router**: The extension maintains a registry of trusted routers; when `sender` is a trusted router, it decodes the real user from `extensionData`.

3. **Architectural fix**: Add an `originator` field to the pool's `swap` call path so the pool can propagate the true end-user address to extensions independently of the router.

---

### Proof of Concept

```
Setup:
  pool = factory.createPool(…, extensions=[swapAllowlist], …)
  swapAllowlist.setAllowedToSwap(pool, alice, true)       // alice is KYC'd
  swapAllowlist.setAllowedToSwap(pool, router, true)      // needed for alice to use the router

Attack (bob, not allowlisted):
  router.exactInputSingle({
      pool:      pool,
      recipient: bob,
      tokenIn:   token0,
      amountIn:  X,
      …
  })
  // router calls pool.swap(bob_recipient, …) → msg.sender in pool = router
  // pool calls extension.beforeSwap(sender=router, …)
  // extension checks allowedSwapper[pool][router] → true → PASSES
  // bob's swap executes at oracle price against LP capital
  // allowlist is completely bypassed
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
