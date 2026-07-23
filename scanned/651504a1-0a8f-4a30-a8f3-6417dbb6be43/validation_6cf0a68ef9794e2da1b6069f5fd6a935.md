### Title
`SwapAllowlistExtension` checks the router's address as `sender` instead of the actual user, allowing any unprivileged user to bypass a per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. The extension therefore checks whether the router is allowlisted, not whether the real economic actor is allowlisted. Any pool admin who allowlists the router (which is required for router-mediated swaps to work at all on that pool) inadvertently opens the pool to every user on the internet.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` identity check:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the only caller allowed by `onlyPool`). `sender` is the first argument, which the pool sets to its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is the **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The dual-role binding problem (analog to H-02):** The `sender` field serves two roles simultaneously — it is both the "identity to gate" and the "immediate caller." When the router intermediates, these two roles diverge. The extension was designed to gate the economic actor (the user who initiates and pays for the swap), but it actually gates the transport layer (the router). This is structurally identical to the H-02 pattern where the same address field is used for two distinct accounting purposes, causing the guard to misfire.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted market makers) must also decide whether to support router-mediated swaps. If the admin allowlists the router address so that allowlisted users can use `MetricOmmSimpleRouter`, the allowlist is completely defeated: **any unprivileged user** can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and the extension will pass because `allowedSwapper[pool][router] == true`. The curated pool becomes an open pool. LP funds are exposed to swappers the admin explicitly intended to exclude, and any price-impact or adverse-selection risk the allowlist was meant to prevent is fully realized.

---

### Likelihood Explanation

The likelihood is **high** for pools that intend to use the router alongside an allowlist. The `MetricOmmSimpleRouter` is the primary user-facing swap entry point in the periphery. A pool admin who wants allowlisted users to be able to use the router has no choice but to allowlist the router address — there is no mechanism in the extension to forward the original `msg.sender` through the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior: any user simply calls the public router.

---

### Recommendation

The `beforeSwap` hook must gate the **economic actor**, not the immediate caller. Two complementary fixes:

1. **In the router:** Forward the original `msg.sender` as the `sender` argument to `pool.swap` (or pass it in `extensionData` in a standardized envelope). The pool would then forward it to the extension.

2. **In `SwapAllowlistExtension`:** If the pool's `sender` is a known trusted router, read the real initiator from a standardized field in `extensionData` rather than trusting the raw `sender` argument.

The simplest correct fix is for `MetricOmmSimpleRouter` to pass `msg.sender` (the actual user) as the `sender` argument to `pool.swap` instead of relying on the pool to use its own `msg.sender`. However, this requires a pool-level interface change to accept an explicit sender. Alternatively, the extension can require that router-mediated calls include the real user address in `extensionData` and verify it against a signature or trusted-forwarder pattern.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is the router.
6. Pool calls `extension.beforeSwap(router, recipient, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully despite not being on the allowlist.

**Corrupted value:** `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][bob]`. The guard passes for an actor it was never configured to permit.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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
