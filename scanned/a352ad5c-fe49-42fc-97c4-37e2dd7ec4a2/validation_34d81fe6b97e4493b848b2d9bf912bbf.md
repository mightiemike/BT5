### Title
`SwapAllowlistExtension` Allowlist Bypassed via Router: Any User Can Swap on Allowlisted Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is always the **router address**, not the actual end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps for EOAs), the per-user allowlist is completely bypassed: any unprivileged user can swap on a restricted pool by calling through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs this check:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (enforced by `onlyPool`). `sender` is the first argument, which the pool sets to its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- immediate caller of pool.swap()
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is used, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `msg.sender` inside `pool.swap()` is the **router**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`.

EOAs cannot call `pool.swap()` directly because the pool immediately calls back `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)` — a callback EOAs cannot implement. This means EOAs **must** use the router. For any router-mediated swap to work, the pool admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] = true` for every call, and the per-user check is permanently bypassed for all users.

The same issue applies to `DepositAllowlistExtension` in the multi-hop `addLiquidityWeighted` probe path: the probe call passes `owner` as the allowlist key, but the payer (`msg.sender`) is the actual economic actor.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is rendered ineffective. Any unprivileged user can route through `MetricOmmSimpleRouter` and execute swaps on the restricted pool. This breaks the core access-control invariant the pool admin configured, and can result in:

- Unauthorized users draining pool liquidity via swaps the admin intended to block.
- LP funds being exposed to unrestricted swap flow, violating the pool's intended operating model.

---

### Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router (which is the standard production setup for any pool that intends to support EOA swaps). No privileged access, no malicious setup, and no special token behavior is needed. Any user who knows the pool address can call `exactInputSingle` on the router.

---

### Recommendation

The extension must gate on the **original user**, not the immediate pool caller. Two approaches:

1. **Pass the original user through the router**: The router should forward `msg.sender` as an explicit `sender` field inside `extensionData`, and the extension should decode and verify it. This requires a coordinated extension+router design.

2. **Check `sender` against a router-aware allowlist**: The pool admin allowlists individual users, and the router is never allowlisted. Instead, the router is trusted to forward the real user identity in `extensionData`, and the extension reads it from there.

3. **Require direct pool interaction for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and provide a direct-call path that EOAs can use (e.g., a permit-based callback wrapper that is itself allowlisted per-user).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended user
  - Bob (not allowlisted) is the attacker

Attack:
  1. Bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...)  →  msg.sender in pool = router
  3. Pool calls _beforeSwap(router, bob, ...)
  4. Extension checks allowedSwapper[pool][router] = true  →  passes
  5. Bob's swap executes; he receives output tokens from the restricted pool

Result:
  - Bob swapped on a pool he was never allowlisted for
  - The allowlist check on `sender` (= router) is satisfied by the router's allowlist entry
  - Per-user restriction is completely bypassed
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
