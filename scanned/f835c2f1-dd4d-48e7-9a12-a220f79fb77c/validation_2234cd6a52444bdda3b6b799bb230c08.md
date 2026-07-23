### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which equals `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router's address. A pool admin who allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the pool to all users, completely bypassing the allowlist.

---

### Finding Description

`SwapAllowlistExtension` is designed to restrict swaps to approved addresses per pool. Its `beforeSwap` hook checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the calling pool and `sender` is the first argument forwarded by the pool. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

So `msg.sender` of the pool = **router address**, not the actual user. The extension checks whether the **router** is allowlisted, not the actual swapper.

This creates an inescapable configuration trap:

- **Router NOT allowlisted**: allowlisted users cannot swap through the router — broken functionality.
- **Router allowlisted** (to enable router-mediated swaps for approved users): any user can bypass the allowlist by routing through the router — security bypass.

There is no configuration that correctly allows specific users to use the router while blocking others.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the economic actor for deposits), not `sender`. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., a private or institutional pool) is fully bypassed the moment the router is allowlisted. Any unprivileged user can swap on the pool by routing through `MetricOmmSimpleRouter`, extracting value from the pool's liquidity at oracle-derived prices. This constitutes broken core pool functionality and direct loss of LP assets, because the pool's intended access boundary is silently removed.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router. A pool admin who wants their approved users to be able to use the primary periphery swap interface (`MetricOmmSimpleRouter`) would naturally allowlist the router, not realizing this opens the pool to everyone. The router is the standard user-facing swap path, making this scenario realistic for any curated pool that intends to support normal UX.

---

### Recommendation

The `SwapAllowlistExtension` must check the actual end-user, not the intermediary router. Concrete options:

1. **Extension-data forwarding**: require the router to encode the real user address in `extensionData`; the extension verifies it matches a signed or trusted claim.
2. **Dedicated sender field**: add a pool-level concept of "originating user" that the router populates and the pool forwards to extensions separately from `msg.sender`.
3. **Minimum documentation guard**: at minimum, document explicitly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`, so pool admins are not misled.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin allowlists userA:
       extension.setAllowedToSwap(pool, userA, true)
3. Pool admin allowlists the router so userA can use the standard UI:
       extension.setAllowedToSwap(pool, router, true)
4. userB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) → msg.sender of pool = router.
6. _beforeSwap(router, ...) → extension checks allowedSwapper[pool][router] = true → passes.
7. userB's swap executes successfully, bypassing the allowlist entirely.
```

The root cause is at: [5](#0-4) 

triggered via: [6](#0-5)

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
