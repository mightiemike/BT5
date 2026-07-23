### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of `MetricOmmPool.swap()`. When a user enters through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. If the router is allowlisted — the natural configuration for any pool that intends to support router-mediated swaps — every unpermissioned user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap(); the router when routed
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(params.recipient, ...)` directly — the pool never sees the original EOA:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

A pool admin who wants to allow router-mediated swaps will allowlist the router address. Once the router is allowlisted, the extension's per-user gate is completely open: any caller of the router passes the check regardless of whether their own address is in the allowlist.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` is designed to restrict which addresses may trade against its liquidity. If the router is allowlisted (the only way to support router users), the restriction is nullified for every user who routes through the router. Unpermissioned users can execute swaps at oracle-anchored prices, extracting value from LP positions that were intended to serve only a controlled set of counterparties. This is a direct loss of LP principal and a broken core pool invariant (curated access).

---

### Likelihood Explanation

The router is the primary production entry point for end users. Any pool that wants to support normal UX must either allowlist the router or require every user to call the pool directly. Allowlisting the router is the obvious operational choice, making this bypass reachable on every curated pool that uses the router. No privileged setup beyond the normal pool configuration is required; any unpermissioned EOA can trigger it.

---

### Recommendation

The extension must gate on the economic actor, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** `MetricOmmSimpleRouter` should forward `msg.sender` as an authenticated field inside `extensionData` (or a dedicated parameter), and `SwapAllowlistExtension` should decode and check that field instead of the raw `sender` argument.

2. **Alternatively, check `sender` only when it is not a known router.** The extension could maintain a registry of trusted routers and, when `sender` is a router, require the real user identity to be supplied and verified via a signed payload in `extensionData`.

The deposit-side extension (`DepositAllowlistExtension`) does not share this flaw because it gates on `owner` (the position recipient), which the liquidity adder passes through unchanged from the caller's explicit argument.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — the natural step to enable router-mediated swaps.
3. Pool admin calls `setAllowedToSwap(pool, userA, true)` — intending to restrict swaps to `userA` only.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, recipient, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` = `true` → no revert.
8. `userB`'s swap executes successfully against the curated pool's liquidity, bypassing the intended per-user gate. [1](#0-0) [2](#0-1) [3](#0-2)

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
