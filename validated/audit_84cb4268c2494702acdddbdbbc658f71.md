### Title
SwapAllowlistExtension Gates on Router Address Instead of End-User, Allowing Any User to Bypass the Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract address**, not the actual end user. If the pool admin allowlists the router (the natural configuration for pools that want to support router-mediated swaps), every user — including those not individually allowlisted — can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool sets `sender` to its own `msg.sender`.**

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

**Step 2 — Router is `msg.sender` to the pool.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The router is therefore `msg.sender` from the pool's perspective: [2](#0-1) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Step 3 — Extension checks the router address, not the end user.**

`SwapAllowlistExtension.beforeSwap` receives `sender` (= router address) and checks it against the allowlist keyed by `(pool, sender)`: [3](#0-2) 

The actual end user's address is never seen by the extension. The extension's `allowedSwapper` mapping is keyed by `(pool → swapper)`, where `swapper` is the router when the call is router-mediated: [4](#0-3) 

**The dilemma this creates for pool admins:**

- If the admin allowlists only specific EOAs (e.g., user A, user B) but **not** the router, those EOAs cannot swap via the router (the router address fails the check), breaking the expected UX.
- If the admin allowlists the router to fix this, **every user** can bypass the per-user allowlist by routing through the router, because the extension sees only the router address and approves it unconditionally.

There is no configuration of `SwapAllowlistExtension` that simultaneously (a) allows allowlisted users to swap via the router and (b) blocks non-allowlisted users from doing the same.

---

### Impact Explanation

The `SwapAllowlistExtension` is a production access-control extension. Its stated purpose is to gate `swap` by swapper address, per pool. When the router is allowlisted (the only way to support router-mediated swaps for any user), the allowlist is rendered completely ineffective: any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and execute a swap against a pool that was intended to be restricted. This is an admin-boundary break — the pool admin's configured restriction is bypassed by an unprivileged path through a public periphery contract.

---

### Likelihood Explanation

Pool admins who deploy a `SwapAllowlistExtension` and also want to support the official router will naturally allowlist the router address. This is the expected operational pattern for any pool that restricts direct swaps but still wants to be accessible via the standard periphery. The bypass is therefore reachable in any realistic deployment that uses both the allowlist extension and the router.

---

### Recommendation

The extension must gate on the **ultimate end user**, not the immediate caller of the pool. Two approaches:

1. **Pass the original user through the router.** Modify `MetricOmmSimpleRouter` to encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Check `recipient` instead of `sender`.** For swap allowlists, `recipient` (the address receiving output tokens) is often the economically relevant identity. However, `recipient` can also be set to an arbitrary address, so this is not a complete fix.

3. **Preferred: use a dedicated router that forwards the original caller.** The pool's `swap()` interface should accept an explicit `payer` or `originator` argument (analogous to how `addLiquidity` separates `owner` from `msg.sender`), and the extension should check that field. Until then, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and must only be used with direct pool calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap slot
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow router-mediated swaps for allowlisted users)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not individually allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
        pool: restrictedPool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
      })
  - Router calls pool.swap(...) → pool sets sender = router address
  - Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → passes
  - Swap executes for attacker despite attacker not being allowlisted

Result:
  - attacker successfully swaps against a pool intended to be restricted
  - SwapAllowlistExtension's per-user gate is completely bypassed
``` [3](#0-2) [1](#0-0) [2](#0-1)

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
