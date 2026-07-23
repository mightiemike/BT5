### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any non-allowlisted user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. The allowlist is keyed per-user, so either allowlisted users cannot swap through the router (broken functionality), or the admin must allowlist the router address — which then lets every user bypass the individual check.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (validated by `onlyPool`), and `sender` is the value forwarded from the pool — the router's address when the user enters via `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`.

The router calls the pool directly without forwarding the original user identity:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

So the extension sees `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Bypass path**: A pool admin who wants to restrict swaps to KYC'd users sets `allowedSwapper[pool][user_A] = true`, `allowedSwapper[pool][user_B] = true`, etc. Those users need the router for slippage protection and multi-hop routes, so the admin must also set `allowedSwapper[pool][router] = true`. Once the router is allowlisted, any non-allowlisted user can call `router.exactInputSingle(...)` and the check passes because `allowedSwapper[pool][router]` is `true`. The individual per-user gate is completely defeated.

---

### Impact Explanation

Any non-allowlisted user can execute swaps on a pool that is supposed to be restricted to a specific set of addresses. This breaks the core pool access-control invariant enforced by `SwapAllowlistExtension`. Depending on the pool's purpose (e.g., institutional-only liquidity, regulatory-gated pools), unauthorized swaps can drain LP assets at oracle-derived prices, cause pool insolvency, or violate compliance requirements that the allowlist was designed to enforce.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery entry point. Any user who discovers the allowlist can trivially route through the router instead of calling the pool directly. No privileged access, no special setup, and no non-standard token behavior is required. The condition that makes the bypass possible (router being allowlisted) is the natural operational state for any pool whose allowlisted users need router functionality.

---

### Recommendation

The extension must check the **original user identity**, not the intermediary. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router or a signed payload.

2. **Check `sender` (the router) and require the router to attest the real user**: Add a registry of trusted routers; for trusted routers, decode the real user from `extensionData` and check that address against the allowlist.

3. **Require direct pool interaction for allowlisted pools**: Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call the pool directly. This is the weakest mitigation.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists user_A: setAllowedToSwap(pool, user_A, true)
  - Pool admin allowlists router (required for user_A to use router):
      setAllowedToSwap(pool, router, true)

Attack:
  - attacker (not in allowlist) calls:
      router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes — attacker bypassed the allowlist
```

The check at [1](#0-0)  evaluates `allowedSwapper[pool][router]` when the call originates from the router, not `allowedSwapper[pool][actual_user]`.

The pool passes `msg.sender` (the router) as `sender` to the extension at [2](#0-1) .

The router calls the pool directly without forwarding the original caller's identity at [3](#0-2) .

The `onlyPool` modifier in `BaseMetricExtension` only validates that the caller is a registered pool — it does not validate which user initiated the action — at [4](#0-3) .

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
