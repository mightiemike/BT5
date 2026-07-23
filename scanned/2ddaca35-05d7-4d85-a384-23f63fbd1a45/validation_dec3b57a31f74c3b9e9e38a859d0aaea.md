### Title
SwapAllowlistExtension Bypass via Router: `sender` Identity Mismatch Allows Unauthorized Swaps on Restricted Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the **immediate caller of `pool.swap()`**. When users route through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the actual end user. If a pool admin allowlists the router address (the natural configuration to support router-mediated swaps for allowlisted users), any non-allowlisted user can bypass the per-user swap allowlist by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension hook.**

In `MetricOmmPool.swap()`, the `sender` forwarded to `_beforeSwap` is `msg.sender` of the pool call: [1](#0-0) 

**Step 2 — `SwapAllowlistExtension.beforeSwap` checks that `sender` against the per-pool allowlist.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool (used as the mapping key) and `sender` = the immediate caller of `pool.swap()`. [2](#0-1) 

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself the `sender`.**

For `exactInputSingle`: [3](#0-2) 

For `exactInput` (multi-hop), every hop is called from the router: [4](#0-3) 

In both cases, `msg.sender` of `pool.swap()` = router address. The actual end user (`msg.sender` of the router call) is never forwarded to the pool or the extension.

**Step 4 — The resulting identity mismatch.**

| Scenario | `sender` seen by extension | Allowlist check |
|---|---|---|
| User calls `pool.swap()` directly | User address | Correct |
| User calls `MetricOmmSimpleRouter.exactInputSingle()` | Router address | Wrong identity |

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check passes for **any** caller of the router, including non-allowlisted users.

**Step 5 — Contrast with `DepositAllowlistExtension`, which correctly gates the actual user.**

The deposit extension checks `owner` (the position owner, the actual user), not `sender` (the liquidity adder contract): [5](#0-4) 

This asymmetry confirms the swap allowlist is checking the wrong identity.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., a private institutional pool) loses that restriction entirely once the router is allowlisted. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle()` and execute swaps against LP funds without authorization. This is a direct loss of LP principal through unauthorized trades at oracle-driven prices, and a complete break of the pool's access-control invariant.

---

### Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is the natural and expected configuration for any pool that wants to support router-mediated swaps for its allowlisted users — there is no other way to enable router access. The attacker needs no special privileges: calling the public `MetricOmmSimpleRouter` is sufficient. The trigger is reachable by any unprivileged user on any pool that has both `SwapAllowlistExtension` and the router allowlisted.

---

### Recommendation

The extension must gate the **actual end user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user in `extensionData`**: Have the router encode `msg.sender` (the actual user) into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. This requires the extension to maintain a trusted-router registry.

2. **Align with the deposit pattern**: Introduce a `swapper` field analogous to `owner` in the liquidity path — a field that always carries the economic principal regardless of who the immediate caller is — and have the pool populate it from the router's forwarded context.

Until fixed, pool admins should be warned that allowlisting the router address effectively opens the pool to all users, defeating the per-user allowlist.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin allowlists Alice (allowedSwapper[pool][alice] = true)
  - Pool admin also allowlists the router (allowedSwapper[pool][router] = true)
    so that Alice can use the router

Attack:
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  - Bob's swap executes against LP funds

Result:
  - Bob bypasses the per-user allowlist
  - Non-allowlisted user trades against LP principal
  - Pool access control is broken
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
