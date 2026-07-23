The core issue is clear from the code. Let me verify the exact flow once more.

The vulnerability is real and provable from the code. Here is the complete analysis:

---

### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension`, Breaking Per-User Allowlist Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the original user. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` where `sender` is the router — so the allowlist gates the router, not the user. This creates an irreconcilable dilemma: either block all router swaps, or allowlist the router and let every user bypass the per-user gate.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook:**

In `MetricOmmPool.swap()`, the `sender` forwarded to `_beforeSwap` is always `msg.sender`: [1](#0-0) 

**Step 2 — Router calls the pool directly; original user is lost:**

`exactInputSingle` (and all other `exact*` functions) call `pool.swap(...)` directly. At the pool, `msg.sender` is the router contract, not the end user: [2](#0-1) 

**Step 3 — The hook checks the router address, not the user:**

`SwapAllowlistExtension.beforeSwap` receives `sender` = router address and checks `allowedSwapper[msg.sender][sender]` (where `msg.sender` = pool, `sender` = router): [3](#0-2) 

The allowlist is keyed `allowedSwapper[pool][swapper]` and is set per individual address by the pool admin: [4](#0-3) 

**The dilemma is irreconcilable:**

| Router allowlist state | Effect |
|---|---|
| Router NOT allowlisted | All router swaps revert with `NotAllowedToSwap()`, even for individually allowlisted users — broken functionality |
| Router IS allowlisted | Every user can bypass the per-user allowlist by routing through the router — allowlist bypass |

There is no configuration that makes the allowlist work correctly for both direct and router-mediated swaps simultaneously.

### Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or whitelisted market makers) cannot enforce that restriction for router-mediated swaps. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` on a restricted pool and either:
- Bypass the allowlist entirely (if the router is allowlisted), or
- Cause all router-path swaps to fail for legitimate allowlisted users.

This breaks the core functionality of the `SwapAllowlistExtension` and constitutes a broken access-control invariant for any pool relying on it.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical public swap interface. Any pool that uses `SwapAllowlistExtension` and expects users to interact via the router is affected. The bypass requires no special privileges — any user can call `exactInputSingle`.

### Recommendation

The pool should pass the original user's identity through to the hook. Two options:

1. **Pass `tx.origin` as an additional field** in `extensionData` from the router, and have the extension decode it — but this is fragile and trust-dependent.
2. **Preferred:** The router should pass the original `msg.sender` as the `recipient` or via a dedicated `extensionData` field, and the extension should read the original sender from `extensionData` rather than the `sender` argument. Alternatively, the pool's `swap` interface could accept an explicit `swapper` parameter distinct from `msg.sender` (the callback payer), similar to how `addLiquidity` separates `msg.sender` (payer/sender) from `owner`.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
   → allowedSwapper[pool][alice] = true
3. Pool admin also calls setAllowedToSwap(pool, router, true)
   (required for any router swap to work).
   → allowedSwapper[pool][router] = true
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...).
   → Router calls pool.swap(...) with msg.sender = router.
   → _beforeSwap(sender=router, ...) is called.
   → beforeSwap checks allowedSwapper[pool][router] == true → PASSES.
5. Bob successfully swaps on a pool that was supposed to be restricted to Alice only.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-19)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
