### Title
SwapAllowlistExtension Allowlist Fully Bypassed via MetricOmmSimpleRouter Intermediary — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct `msg.sender` of `pool.swap()`. When any user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual end-user. If the router is allowlisted (the natural configuration for a pool that supports router-mediated swaps), the allowlist is completely bypassed and any unprivileged user can trade in a pool intended to be restricted.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the direct caller of `pool.swap()`. The effective check is `allowedSwapper[pool][sender]`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(recipient, ...)` with itself as `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol L71-80
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

The actual end-user (`msg.sender` of `exactInputSingle`) is stored only in transient storage as the payment payer — it is never forwarded to the pool or the extension. The pool sees `msg.sender = router` and passes `sender = router` to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user on-chain bypasses the allowlist by calling the router |
| Router **is not** allowlisted | Allowlisted users cannot use the router at all; their swaps revert |

The same structural problem applies to the multi-hop `exactInput` path, where intermediate hops use `address(this)` (the router) as the payer identity, and to `exactOutput` / `exactOutputSingle`.

The root cause is that the router stores the real user identity only in transient storage for payment settlement, but never passes it through the `pool.swap()` call where the extension can read it. There is no trusted channel for the extension to recover the original caller.

---

### Impact Explanation

A pool deployer configures `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified institutions, whitelisted market makers, or private LP partners). The pool offers tight bid/ask spreads calibrated for those trusted counterparties. When the pool admin also allowlists the router (the standard UX path), any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade at the restricted pool's favorable rates. The LPs suffer direct losses from trades they explicitly intended to block. The allowlist provides zero protection once the router is allowlisted.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who wants allowlisted users to have a normal UX must allowlist the router. This is the expected and natural configuration. The bypass is therefore reachable in any realistic deployment of a restricted pool that also supports router-mediated swaps. No special privileges, flash loans, or unusual token behavior are required — any EOA can call the router.

---

### Recommendation

The router must forward the original caller's identity to the pool in a way the extension can trust. Two options:

1. **Trusted forwarder pattern**: Add a `swapper` field to the pool's `swap` call parameters (or a dedicated trusted-forwarder registry). The router passes `msg.sender` explicitly; the pool forwards it as `sender` to the extension instead of its own `msg.sender`.

2. **Extension-side transient read**: The router writes the real user address into a well-known transient slot before calling the pool; the extension reads that slot. This requires a shared transient-storage convention between the router and the extension.

Option 1 is cleaner and does not require out-of-band coordination.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists only `trustedUser` and the router (for UX).
// allowedSwapper[pool][trustedUser] = true
// allowedSwapper[pool][router]     = true   ← natural config

// Attacker (not allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool:            restrictedPool,
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    tokenIn:         token0,
    extensionData:   ""
}));
// pool.swap(msg.sender=router) → extension.beforeSwap(sender=router)
// allowedSwapper[pool][router] == true → check passes
// Attacker trades at restricted pool's favorable rates. LPs lose.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
