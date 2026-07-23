### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool (i.e., the router contract), not the original EOA. When a pool admin allowlists the `MetricOmmSimpleRouter` to enable router-based swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly: [2](#0-1) 

The pool therefore passes the **router's address** as `sender` to every extension hook. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. If the router is in the allowlist (a natural admin action so that permitted users can use the router), the guard passes for **every caller of the router**, regardless of who they are.

The allowlist is keyed by `(pool → swapper → bool)` and is intended to gate individual swapper identities: [4](#0-3) 

But the identity actually checked on the router path is the router's address, not the originating EOA. The extension has no mechanism to recover the original caller once the pool has already replaced it with `msg.sender`.

---

### Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is fully open to any user who routes through `MetricOmmSimpleRouter`. The allowlist policy — which may restrict trading to KYC'd counterparties, specific market makers, or protocol-controlled addresses — is silently nullified. Any non-allowlisted user can execute swaps, receiving pool output tokens and draining LP-owned liquidity at oracle-derived prices. This is a direct loss of LP principal and a complete curation failure on the affected pool.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router, which is the natural and expected action for any pool that wants its permitted users to be able to use the standard periphery. A pool admin who adds specific EOAs to the allowlist and also adds the router (to let those EOAs use it) unknowingly opens the pool to all users. The router is a public, permissionless contract, so any EOA can exploit this immediately after the router is allowlisted. No privileged access, flash loan, or multi-step setup is required beyond a single `exactInputSingle` call.

---

### Recommendation

The `SwapAllowlistExtension` must check the **original EOA**, not the direct pool caller. Two complementary fixes:

1. **Short term:** In `beforeSwap`, ignore the `sender` argument and instead require that `msg.sender` (the pool) exposes the original initiator via a dedicated getter (e.g., a transient-storage `initiator()` view on the pool, set at the top of `swap()` to the pool's own `msg.sender`). The extension then reads `IMetricOmmPool(msg.sender).initiator()` and checks that address against the allowlist.

2. **Long term:** Document clearly that `sender` in extension hooks is the direct caller of the pool, not the originating EOA, and require all access-control extensions to use the pool-level initiator pattern rather than the forwarded `sender` argument.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  admin allowlists Alice (EOA) and router (MetricOmmSimpleRouter)
  Eve (not allowlisted) holds token0

Attack:
  Eve calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: Eve,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  Router → pool.swap(Eve, true, X, ...)
    pool._beforeSwap(router, Eve, ...)
      SwapAllowlistExtension.beforeSwap(router, ...)
        allowedSwapper[pool][router] == true  ← passes
    swap executes, Eve receives token1

Result: Eve swaps on a curated pool she was never permitted to access.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
