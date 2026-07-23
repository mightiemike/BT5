### Title
`SwapAllowlistExtension` checks the direct pool caller (`sender`) rather than the originating user, allowing any user to bypass the swap allowlist by routing through `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. A pool admin who wants allowlisted users to be able to use the official router must allowlist the router address itself. Because the router is a public, permissionless contract, allowlisting it silently opens the pool to every user, completely defeating the curation policy.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap` call:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

So when any user calls through the router, the extension receives `sender = router_address`, not the originating user's address. The extension then evaluates `allowedSwapper[pool][router]`.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all (their individual allowlist entry is irrelevant because the extension sees the router address) |
| **Allowlist the router** | Every user on the network can bypass the allowlist by calling through the router, because `allowedSwapper[pool][router] = true` |

The router carries no access control of its own — it is a fully public contract. Allowlisting it is therefore equivalent to setting `allowAllSwappers[pool] = true`. [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institution-only) with `SwapAllowlistExtension` and then allowlists the router to support standard periphery usage inadvertently opens the pool to all users. Any non-allowlisted address can execute swaps against the pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). The allowlist guard is completely bypassed, allowing unauthorized parties to trade against LP capital that was deposited under the assumption of a restricted counterparty set. This constitutes a direct policy bypass with fund-impacting consequences for LPs.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the protocol's official swap periphery. Pool admins operating curated pools will naturally want their allowlisted users to be able to use it. The only on-chain path to enable this is to allowlist the router address, which is a single `setAllowedToSwap(pool, router, true)` call. There is no documentation or on-chain guard warning the admin that this action disables the allowlist for all users. The likelihood that at least one curated pool makes this configuration is high.

---

### Recommendation

The extension must check the economically relevant actor — the originating user — not the technical caller. Two approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the originating `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it (with a trust assumption on the router, which must itself be verified as a factory-registered contract).

2. **Check `sender` only when caller is a known router**: In `beforeSwap`, if `msg.sender` (the pool) reports that the direct caller is a factory-registered router, fall back to checking the `sender` argument as the user identity; otherwise check the direct caller. This requires the pool or factory to expose a router registry.

The simplest safe fix is option 1: require that any allowlisted router forwards the original user address in `extensionData`, and reject calls from the router that do not carry a valid signed or encoded user identity that passes the allowlist check.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  alice = allowlisted user  (allowedSwapper[pool][alice] = true)
  bob   = non-allowlisted user

Step 1 — Admin enables router support for alice:
  admin calls setAllowedToSwap(pool, router, true)
  // admin intends to let alice use the router
  // but this also allows bob

Step 2 — Bob bypasses the allowlist:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

Step 3 — Extension check:
  pool.swap(msg.sender=router, ...)
  → _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → allowedSwapper[pool][router] == true  ✓
  → swap executes for bob with no revert

Result: bob swaps successfully against a pool that was intended to be restricted to alice only.
``` [5](#0-4) [4](#0-3) [2](#0-1)

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
