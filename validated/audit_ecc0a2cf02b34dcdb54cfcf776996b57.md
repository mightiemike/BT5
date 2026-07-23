The code confirms all three key claims. Let me verify the exact lines:

- `SwapAllowlistExtension.beforeSwap` at line 37 checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the direct caller of `pool.swap()`. [1](#0-0) 
- `MetricOmmPool.sol` at line 230-231 passes `msg.sender` (the direct caller of `pool.swap()`) as `sender` to `_beforeSwap`. [2](#0-1) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly at line 72-80, making the router the `msg.sender` to the pool. [3](#0-2) 

All code paths match the claim exactly. The finding is valid.

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][sender]` where `sender` is always the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router address to enable router-based swaps for their permitted users inadvertently opens the pool to every user of the public router, defeating the per-user access control entirely.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmPool.swap` always passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly without forwarding the original user identity:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
```

The router's `msg.sender` to the pool is the router contract address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. There is no mechanism in the current design for the extension to recover the original end user's address from the call stack or `extensionData`.

The structural trap: a pool admin who wants their allowlisted users to use the standard periphery router must add the router to the allowlist. This single action opens the pool to every caller of the public, permissionless router. The admin cannot achieve per-user gating for router-mediated swaps with the current extension design.

## Impact Explanation
This is an admin-boundary break: an unprivileged path (routing through the public `MetricOmmSimpleRouter`) circumvents the access-control policy the pool admin intended to enforce via `SwapAllowlistExtension`. Any unpermissioned address can execute swaps against a curated pool by routing through the public router, provided the pool admin has allowlisted the router address. This breaks the core invariant of the extension — that only explicitly permitted addresses may swap — and constitutes a broken core pool functionality causing unauthorized access to a restricted pool.

## Likelihood Explanation
The trigger requires the pool admin to have allowlisted the router address. This is a natural and expected action for any admin who wants their permitted users to use the standard periphery router. The mistake is non-obvious: the admin may believe they are enabling router access for their specific users, not for all users globally. The router is a public, immutable, permissionless contract, so once it is allowlisted, the bypass is available to any address with no further preconditions.

## Recommendation
The extension must check the actual end user, not the intermediary. Two approaches:

1. **Trusted-forwarder pattern:** The router encodes `msg.sender` into `extensionData`; the extension verifies the forwarder's identity (e.g., checks `sender == trustedRouter`) before decoding and checking the real user address from `extensionData`.
2. **Router-level enforcement:** Require the router to enforce its own allowlist before calling `pool.swap`, and document clearly that `SwapAllowlistExtension` only gates direct pool callers.

At minimum, document that allowlisting the router address opens the pool to all router users and that per-user gating of router-mediated swaps is not supported by the current design.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  pool admin: swapExtension.setAllowedToSwap(pool, alice, true)
  pool admin: swapExtension.setAllowedToSwap(pool, router, true)
    ↑ admin intends to let alice use the router; unknowingly opens pool to all

Attack:
  charlie (not in allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

  Router calls:
    pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender to pool = router

  Pool calls:
    _beforeSwap(router, recipient, ...)

  Extension evaluates:
    allowedSwapper[pool][router] → true   ← charlie bypasses the gate

  Result: charlie's swap executes successfully against the curated pool
```

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
