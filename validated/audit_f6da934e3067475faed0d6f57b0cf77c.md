All cited code is confirmed in the repository. The vulnerability is real and the call chain is exactly as described:

1. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the first argument. [1](#0-0) 

2. `MetricOmmPool.sol` passes `msg.sender` (the immediate caller of `pool.swap`) as the `sender` argument to `_beforeSwap`. [2](#0-1) 

3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the pool see `msg.sender = router`. [3](#0-2) 

4. The `onlyPool` guard only verifies the caller is a registered pool — it does not recover the original end user. [4](#0-3) 

The router passes `params.extensionData` through unchanged but the extension never reads it — there is no existing mechanism to recover the real swapper. The bypass requires no special privileges and is reachable by any EOA via a single `exactInputSingle` call once the router is allowlisted.

---

Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, but `sender` is `msg.sender` from the pool's perspective — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that caller is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps simultaneously opens the allowlist to every user on the network, defeating the curation entirely.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (enforced by `onlyPool`) and `sender` is whatever the pool passed as its first argument to `_beforeSwap`. `MetricOmmPool.swap` always passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
  msg.sender,   // ← immediate caller of pool.swap(), not the economic actor
  recipient,
  ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

From the pool's perspective `msg.sender` is the **router address**, so the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The same applies to `exactInput` multi-hop paths, `exactOutputSingle`, and `exactOutput`. The `onlyPool` guard in `BaseMetricExtension` only verifies the caller is a registered pool; it does not recover the original end user. The router passes `params.extensionData` through unchanged, but the extension never reads it — there is no existing mechanism to recover the real swapper.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants to support the standard periphery router must call `setAllowedToSwap(pool, router, true)`. Once that entry exists, **any address** can call `router.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension will approve the swap because it sees `sender = router`. The individual per-user allowlist is completely bypassed. All pool liquidity is exposed to unrestricted swapping, which can drain LP value through arbitrage or other strategies the allowlist was meant to prevent. This constitutes broken core pool functionality causing loss of funds and an admin-boundary break where the pool admin's access control configuration is rendered ineffective by an unprivileged path.

## Likelihood Explanation
The router is the canonical periphery entry point documented and expected by integrators. Any pool admin who configures a swap allowlist and also wants router support will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no multi-block setup — a single `exactInputSingle` call from any EOA suffices. The precondition (router being allowlisted) is the expected operational state for any pool that intends to support standard router usage.

## Recommendation
The extension must gate the **economic actor**, not the immediate `pool.swap()` caller. Two sound approaches:

1. **Pass the original user through extension data**: The router encodes `msg.sender` into `extensionData` and the extension reads it from there, verifying the pool is the caller (already enforced by `onlyPool`) and the encoded address is the real swapper.

2. **Check both router and end user**: The extension allowlist entry for the router is replaced by a two-level check: if `sender` is a known router, decode the real user from `extensionData` and check that address instead.

Either way, the allowlist must resolve to the address that controls the economic decision to swap, not the contract that mechanically forwards the call.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)
   — only alice is intended to swap.
3. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so alice can use the standard router.
4. bob (not allowlisted) calls:
     router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) with msg.sender = router.
6. Pool calls extension.beforeSwap(router, ...).
7. Extension evaluates:
     allowedSwapper[pool][router] == true  →  no revert.
8. bob's swap executes successfully despite not being on the allowlist.
```

The corrupted value is `sender` delivered to `beforeSwap`: it is `router` (the forwarder) instead of `bob` (the economic actor), causing the allowlist lookup to resolve against the wrong key and return `true` for an address that should have been rejected.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
