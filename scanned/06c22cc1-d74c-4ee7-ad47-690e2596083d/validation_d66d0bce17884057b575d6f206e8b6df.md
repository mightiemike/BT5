### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged swapper to bypass the per-pool allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the allowlist. Conversely, if the admin allowlists only individual users, those users cannot swap through the router at all. The `DepositAllowlistExtension` does not share this flaw — it correctly gates the `owner` parameter (the economic actor), not the `sender`.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards this value unchanged to every configured extension. `SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` — so `sender` = router address, not the end user.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position owner explicitly supplied by the caller), not `sender`:

```solidity
// DepositAllowlistExtension.sol line 38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

`owner` is preserved correctly through `MetricOmmPoolLiquidityAdder` because the adder passes the caller-supplied `owner` directly to `pool.addLiquidity()`. No equivalent user-identifying field exists on the swap path.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address (a natural step to enable router-mediated swaps for their allowlisted users) inadvertently opens the pool to every user. Any address can call `router.exactInputSingle()` and the extension will pass because `allowedSwapper[pool][router] == true`. The intended per-user curation is silently voided. LPs in a curated pool (e.g., one restricted to trusted market-makers to avoid adverse selection) suffer unrestricted exposure to uninvited counterparties, constituting a direct loss of LP value through bad-price execution that the configured protection was supposed to prevent.

---

### Likelihood Explanation

The scenario is reachable through normal, non-malicious admin actions. A pool admin who wants allowlisted users to be able to use the standard router will call `setAllowedToSwap(pool, router, true)`. This is the obvious and expected configuration step. No privileged attacker action, malicious setup, or non-standard token is required. Any user who discovers the router is allowlisted can immediately exploit it.

---

### Recommendation

Gate swaps on the end user rather than the immediate caller. Two options:

1. **Mirror the deposit pattern**: Add an explicit `swapper` parameter to the swap path (analogous to `owner` on the liquidity path) that the router populates with `msg.sender` before calling `pool.swap()`. The extension then checks this field.

2. **Short-term mitigation**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and that allowlisting the router address voids per-user curation. Require direct `pool.swap()` calls for allowlisted pools.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)      // intends to allow only alice
  admin calls setAllowedToSwap(pool, router, true)     // enables router path for alice

Attack:
  bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient=bob, ...)
  pool calls extension.beforeSwap(sender=router, ...)
  extension checks allowedSwapper[pool][router] == true  → PASS
  bob's swap executes against the curated pool's liquidity

Result:
  bob bypasses the allowlist entirely
  alice's exclusive access to the pool is voided
  LPs suffer unrestricted adverse-selection exposure
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
