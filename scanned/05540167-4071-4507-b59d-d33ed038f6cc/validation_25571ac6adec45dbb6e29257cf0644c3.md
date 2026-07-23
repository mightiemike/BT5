### Title
`SwapAllowlistExtension` gates on the router's address instead of the original swapper, allowing any user to bypass a curated pool's allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the **router**, not the original user. If the pool admin allowlists the router (the natural step to enable router-based swaps for their curated users), every unprivileged user can bypass the individual allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.** [1](#0-0) 

`_beforeSwap` is called with `msg.sender` as the first argument. When the router calls `pool.swap()`, `msg.sender` inside the pool is the router contract, not the original EOA.

**Step 2 — The extension checks `sender` (the router) against the allowlist.** [2](#0-1) 

`allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router]`. The original user's address is never visible to the extension.

**Step 3 — The router stores the original user only in transient payment context, never forwarding it to the pool.** [3](#0-2) 

`msg.sender` (the real swapper) is stored as the payer for the callback, but the pool's `swap()` call receives no user-identity argument. The extension has no path to the original user.

**The bypass chain:**

1. Pool admin creates a curated pool with `SwapAllowlistExtension` and allowlists Alice: `allowedSwapper[pool][alice] = true`.
2. Pool admin also allowlists the router so Alice can use it: `allowedSwapper[pool][router] = true`.
3. Bob (not individually allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` — `msg.sender` in the pool is the router.
5. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
6. Bob's swap executes in the curated pool, bypassing the intended per-user gate.

---

### Impact Explanation

Any unprivileged user can trade in a pool that the admin intended to restrict to a named set of addresses. The allowlist provides zero protection once the router is allowlisted. Depending on the pool's purpose (e.g., preferential pricing, regulatory KYC gate, LP-only access), this can result in direct loss of LP assets or protocol fees, or a complete breakdown of the pool's access-control invariant.

---

### Likelihood Explanation

The pool admin **must** allowlist the router to let their approved users trade through the standard periphery path. There is no other supported mechanism: the router always calls `pool.swap()` as itself. A pool admin who sets up a curated pool and then enables router access (a routine operational step) unknowingly opens the pool to everyone. The trigger is a valid, expected admin action, not a malicious one.

---

### Recommendation

The extension must gate on the **economically responsible actor**, not the direct caller of `pool.swap()`. Two viable fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check it. This requires a convention between the router and the extension.
2. **Extension-side**: Change `beforeSwap` to check `sender` only when `sender` is not a known router, and separately check the payer identity via a shared transient-storage interface. This is architecturally cleaner but requires a payer-forwarding protocol.

The simplest safe default: document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and remove the per-user allowlist path when the router is in use.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists Alice for direct access
ext.setAllowedToSwap(pool, alice, true);
// Pool admin allowlists the router so Alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// Attack: Bob (not allowlisted) routes through the router
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            pool,
    tokenIn:         token0,
    recipient:       bob,
    zeroForOne:      true,
    amountIn:        1000,
    amountOutMinimum: 0,
    priceLimitX64:   type(uint128).max,
    deadline:        block.timestamp + 1,
    extensionData:   ""
}));
// Bob's swap succeeds — allowlist bypassed
```

The extension sees `sender = address(router)`, which is allowlisted, so the check passes for Bob even though `allowedSwapper[pool][bob]` is `false`.

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
