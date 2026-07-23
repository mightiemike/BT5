### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` (the immediate caller of `pool.swap()`). When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist therefore gates the router address, not the human trader. If the pool admin allowlists the router to support router-mediated swaps for legitimate users, every unprivileged address can bypass the per-user allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the first argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`_beforeSwap` forwards that value as `sender` to each configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The guard resolves to `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without forwarding the originating user's address:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The pool never sees the end user's address; it only sees the router. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

The pool admin faces an irresolvable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every address on-chain can bypass the per-user allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to trade through the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A curated pool (e.g., KYC-gated, institutional-only, or compliance-restricted) that deploys `SwapAllowlistExtension` to restrict trading to approved addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user receives the full swap output; the pool's LP assets are consumed exactly as in a legitimate swap. This is a direct, fund-impacting bypass of a core access-control invariant: LP assets are exposed to counterparties the pool was explicitly configured to exclude.

---

### Likelihood Explanation

The router is a public, permissionless contract. Any non-allowlisted user can call `exactInputSingle` or `exactInput` with no special privileges. The only precondition is that the pool admin has allowlisted the router — a natural step any admin would take when they want their allowlisted users to benefit from router features (slippage protection, multi-hop, deadline checks). The admin has no way to know that allowlisting the router opens the gate to everyone.

---

### Recommendation

Pass the originating user's address through the router to the pool, and have the pool forward it to extensions as a separate `originator` argument distinct from `sender`. Alternatively, `SwapAllowlistExtension` should read the originating user from a trusted router registry or from transient storage set by the router before calling the pool, so the guard always checks the economically relevant actor rather than the intermediate contract.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → **passes**.
7. Bob's swap executes and he receives output tokens from the pool, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
