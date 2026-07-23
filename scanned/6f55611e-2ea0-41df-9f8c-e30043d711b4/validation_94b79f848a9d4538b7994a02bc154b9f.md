### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct `msg.sender` of `pool.swap()`. When any user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for their allowlisted users, every unprivileged address can bypass the individual allowlist and swap against the restricted pool.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its gate as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the argument forwarded by the pool from its own `msg.sender`:

```solidity
// MetricOmmPool.swap
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), NOT the end-user
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is called, the router itself calls `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.exactInputSingle
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

`msg.sender` inside `pool.swap()` is the **router**, so the extension evaluates `allowedSwapper[pool][router]` — never the actual end-user.

This creates an irresolvable configuration dilemma for pool admins:

| Admin action | Result |
|---|---|
| Allowlist individual users only, not the router | Allowlisted users **cannot** swap through the router |
| Allowlist the router (to enable router-mediated swaps) | **Every** address can bypass the individual allowlist |

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position owner passed explicitly by the pool), not `sender`, so it correctly gates the economic actor regardless of who calls `addLiquidity`. The swap extension should mirror this design but does not.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., to prevent adverse selection against LPs, or to implement a private institutional pool) is fully open to any address that routes through `MetricOmmSimpleRouter` once the admin allowlists the router. Unauthorized swappers can execute oracle-priced swaps against the pool, exposing LPs to adverse selection and potential principal loss if the oracle is stale or the pool's restricted design was the only protection against informed flow.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router — a natural and expected step for any admin who wants their allowlisted users to be able to use the standard periphery. The admin has no on-chain signal that doing so opens the pool to all users; the allowlist UI and docs give no indication of this identity-substitution problem.

---

### Recommendation

Check the actual end-user identity rather than the direct `pool.swap()` caller. Two viable approaches:

1. **Router forwards user identity in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it (requires trust that the router is the caller, which `onlyPool` + `msg.sender == router` can enforce).
2. **Mirror the deposit pattern**: Introduce an explicit `swapper` parameter (analogous to `owner` in `addLiquidity`) that the pool passes through from the original user, decoupled from `msg.sender`.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router
4. bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(...)  [msg.sender = router]
   → pool calls _beforeSwap(router, ...)
   → extension checks allowedSwapper[pool][router] → true
   → bob's swap succeeds despite not being individually allowlisted
``` [1](#0-0) 

The `sender` argument checked by the extension is `msg.sender` of `pool.swap()`, which is the router when routing through periphery. [2](#0-1) 

The pool passes its own `msg.sender` (the router) as `sender` to `_beforeSwap`. [3](#0-2) 

The router calls `pool.swap()` directly, substituting itself as `msg.sender` and providing no mechanism to forward the original user's identity. [4](#0-3) 

By contrast, `DepositAllowlistExtension` checks `owner` (the position owner), not `sender`, correctly gating the economic actor regardless of who calls `addLiquidity` — the design the swap extension should mirror.

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
