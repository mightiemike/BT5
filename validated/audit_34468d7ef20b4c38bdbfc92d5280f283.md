### Title
`SwapAllowlistExtension::beforeSwap` checks the router's address instead of the actual end user, making the allowlist bypassable via `MetricOmmSimpleRouter` and blocking allowlisted users from using the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **actual trader** is allowlisted. This produces two symmetric failures: (1) allowlisted users who call through the router are blocked even though they are individually permitted, and (2) if the pool admin allowlists the router address to enable router-based swaps, every user—including non-allowlisted ones—bypasses the per-user gate.

---

### Finding Description

**Pool → Extension argument binding**

In `MetricOmmPool::swap`, the pool passes `msg.sender` as the `sender` argument to every before-swap hook:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`, so `msg.sender` inside the pool is the **router address**, not the end user.

**Extension check**

`SwapAllowlistExtension::beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is whoever called `pool.swap()`. When the router is the caller, the check becomes `allowedSwapper[pool][router]`, completely ignoring the actual end user.

**Two failure modes**

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No (default) | **Reverts** — broken core swap flow | Reverts (correct) |
| Yes (admin enables router) | Passes (correct) | **Passes** — allowlist bypassed |

The first row requires no privileged action and is the default state: any pool that uses `SwapAllowlistExtension` and expects allowlisted users to interact through the router will silently block them. The second row is the bypass: a pool admin who allowlists the router address (a natural configuration to "enable router swaps") inadvertently opens the gate to every user.

---

### Impact Explanation

**Broken core swap flow (no privileged action required):** Allowlisted users on curated pools cannot use `MetricOmmSimpleRouter`—the primary user-facing swap entry point—even though they are individually permitted. Their only option is to call `pool.swap()` directly, bypassing all periphery slippage and routing helpers. This is an unusable swap flow for the intended user set.

**Allowlist bypass (semi-trusted trigger):** A pool admin who allowlists the router address to enable router-based swaps unknowingly grants every user the ability to swap on the curated pool. Non-allowlisted users can trade on pools that were designed to restrict access (e.g., for regulatory or risk-management reasons), which constitutes a policy bypass with direct fund-flow consequences.

---

### Likelihood Explanation

The DoS failure mode is the default state for any pool that combines `SwapAllowlistExtension` with the router. No special configuration is needed to trigger it—it fires the moment an allowlisted user tries to use the router. The bypass failure mode requires the pool admin to allowlist the router, which is a natural and non-malicious configuration choice for a pool that wants to support router-based swaps.

---

### Recommendation

The extension must check the actual end user, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Add a dedicated `originalSender` field to the hook interface**: The pool could forward both `msg.sender` (the immediate caller) and an optional `originalSender` (set by the router via a transient-storage context), letting extensions choose which identity to gate.

Until fixed, pools that require per-user swap gating should not rely on `SwapAllowlistExtension` for router-mediated flows.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as a before-swap hook
  - allowedSwapper[pool][alice] = true   (alice is individually allowlisted)
  - allowedSwapper[pool][router] = false (router is NOT allowlisted)

Scenario A — DoS for allowlisted user:
  alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
    → router calls pool.swap(...) with msg.sender = router
    → pool calls extension.beforeSwap(router, ...)
    → allowedSwapper[pool][router] == false → revert NotAllowedToSwap
  alice calls pool.swap(...) directly
    → pool calls extension.beforeSwap(alice, ...)
    → allowedSwapper[pool][alice] == true → succeeds

Scenario B — Bypass when router is allowlisted:
  Admin sets allowedSwapper[pool][router] = true
  bob (not individually allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
    → router calls pool.swap(...) with msg.sender = router
    → pool calls extension.beforeSwap(router, ...)
    → allowedSwapper[pool][router] == true → succeeds (bypass)
```

The root cause is identical in structure to the reported investor-counter bug: the guard checks a proxy identity (the router / the wallet address) rather than the canonical entity (the actual end user / the investor), and fails to account for the case where the checked identity does not represent the economically relevant actor.

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
