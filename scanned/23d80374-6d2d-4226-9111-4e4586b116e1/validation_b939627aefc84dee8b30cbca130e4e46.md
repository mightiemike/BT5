### Title
`SwapAllowlistExtension` checks router address instead of actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the user. If the pool admin allowlists the router address (a necessary step to enable any router-mediated swap for their users), every unprivileged user can bypass the per-user allowlist by calling the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`msg.sender` is the direct caller of `pool.swap()`. When the user goes through `MetricOmmSimpleRouter`, that direct caller is the router contract, not the user.

**Step 2 — Router calls `pool.swap()` with itself as `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `IMetricOmmPoolActions(params.pool).swap(...)` directly: [2](#0-1) 

There is no mechanism to forward the original `msg.sender` (the user) to the pool. The pool therefore sees `msg.sender = address(router)`.

**Step 3 — Extension checks the router address, not the user.**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the argument passed by the pool (i.e., the router address): [3](#0-2) 

**Step 4 — The bypass.**

For any allowlisted user to swap through the router, the pool admin must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check at line 37 passes for **every** caller of the router — including users who were never individually allowlisted.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully bypassed. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the restricted pool, draining LP assets at oracle-derived prices without authorization. The pool's access-control invariant — that only allowlisted addresses may trade — is broken.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a natural and expected operational step: allowlisted users who want to use the router (e.g., for slippage protection, multi-hop, or deadline enforcement) cannot do so unless the router is allowlisted, because the extension will reject the router's address. The pool admin has no way to allowlist the router for specific users only — it is an all-or-nothing grant. The likelihood is therefore **medium**: any pool that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter` and enables router access for its users is fully exposed.

---

### Recommendation

The `sender` argument forwarded to extensions should represent the economically relevant actor (the end user), not the intermediary contract. Two concrete fixes:

1. **Router-side**: `MetricOmmSimpleRouter` encodes the original `msg.sender` into `extensionData` and `SwapAllowlistExtension` reads it from there when the direct caller is a known router.
2. **Extension-side**: `SwapAllowlistExtension` additionally checks `allowedSwapper[pool][msg.sender_of_pool_call]` only when `msg.sender_of_pool_call` is not a registered router; for registered routers it falls back to a user-identity field in `extensionData`.

The simplest safe fix is to have the router append `abi.encode(msg.sender)` to `extensionData` and have the extension decode and check that value when the direct caller is the router.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
  alice wants to use the router → pool admin calls setAllowedToSwap(pool, router, true)

Attack:
  charlie (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool: restrictedPool,
      recipient: charlie,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=charlie, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes)
        → swap executes, charlie receives tokens
```

`charlie` successfully swaps against the restricted pool despite never being individually allowlisted, because the router address satisfies the allowlist check.

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
