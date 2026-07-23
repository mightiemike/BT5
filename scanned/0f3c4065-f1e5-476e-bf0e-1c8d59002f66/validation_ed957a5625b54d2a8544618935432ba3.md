### Title
`SwapAllowlistExtension.beforeSwap()` checks the router address as `sender` instead of the actual end user, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is the production extension that gates `swap` by swapper address on curated pools. Its `beforeSwap` hook checks the `sender` argument supplied by the pool, which is always `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual end user. If the router is allowlisted (a natural operational choice), every user — including those explicitly excluded from the allowlist — can bypass the curation gate by routing through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension.beforeSwap()`**

`SwapAllowlistExtension.beforeSwap()` receives `sender` as its first argument and checks it against the per-pool allowlist:

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

`msg.sender` here is the pool (the pool calls the extension). `sender` is whatever the pool passed as the first argument to `beforeSwap`.

**What the pool passes as `sender`**

`MetricOmmPool.swap()` passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← immediate caller of pool.swap()
    recipient,
    ...
);
```

**What `msg.sender` is when the router is used**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly:

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

The router is `msg.sender` of `pool.swap()`, so the extension receives `sender = router`. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The bypass**

A pool admin who wants to allow router-based swaps while restricting direct access to specific users will allowlist the router. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router, regardless of who the actual end user is. Any address — including those explicitly excluded from the allowlist — can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the extension check passes unconditionally.

**Contrast with `DepositAllowlistExtension`**

`DepositAllowlistExtension.beforeAddLiquidity()` correctly ignores `sender` and checks `owner` (the position owner), which is an explicit caller-supplied identity that survives router indirection:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The swap interface has no equivalent "actual user" field; `sender` is the only identity the extension receives, and it collapses to the router address on every router-mediated call.

---

### Impact Explanation

Any user excluded from a curated pool's swap allowlist can trade on that pool by routing through `MetricOmmSimpleRouter`. The curation invariant — "only allowlisted addresses may swap" — is broken for every pool that allowlists the router. Depending on the pool's purpose (KYC gating, institutional-only access, regulatory compliance), this constitutes a direct policy bypass with fund-impacting consequences: disallowed users execute swaps that consume LP liquidity and generate fees on a pool that was configured to exclude them.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. This is a natural operational choice: a pool admin who wants to support the standard periphery UX while still restricting direct pool access will allowlist the router. The admin has no indication from the extension's interface or documentation that allowlisting the router implicitly grants access to all users. The `SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool," which implies per-user granularity that does not survive router indirection.

---

### Recommendation

The `beforeSwap` hook should gate on the actual economic actor, not the immediate caller. Two options:

1. **Add an explicit `actualUser` field to the swap interface** (analogous to the ZLS1-1 fix): the pool passes a caller-verified identity alongside `sender`, and the extension checks that field.

2. **Check `recipient` with a documented convention**: require that the router always sets `recipient` to the actual end user and document that the allowlist checks `recipient`. This is weaker because `recipient` is caller-controlled, but it is the only other identity available in the current interface.

Until the interface is extended, pool admins must be warned that allowlisting the router in `SwapAllowlistExtension` grants unrestricted swap access to all users of that router.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` to allow router-based swaps.
3. Pool admin calls `swapExtension.setAllowedToSwap(pool, userB, false)` (or simply never allowlists `userB`).
4. `userB` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: userB, ...})`.
5. Router calls `pool.swap(userB, ...)` — `msg.sender` of `pool.swap()` = router.
6. Pool calls `extension.beforeSwap(router, userB, ...)` — `msg.sender` = pool, `sender` = router.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `userB`'s swap executes successfully despite being excluded from the allowlist.

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
