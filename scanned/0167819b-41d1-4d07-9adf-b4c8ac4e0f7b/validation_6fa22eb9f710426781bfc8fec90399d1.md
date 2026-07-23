### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the original user. This makes it impossible to correctly enforce a per-user allowlist for router-mediated swaps: if the router is allowlisted, every user bypasses the gate; if it is not, allowlisted users cannot use the router at all.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that value against the per-pool allowlist: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [3](#0-2) 

The original user's address is stored only in the transient callback context (for payment), and is never forwarded to the pool as the swap initiator: [4](#0-3) 

The same pattern applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

Because the extension sees `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`, a pool admin who allowlists the router address (the natural action to enable router-mediated swaps for their allowlisted users) inadvertently opens the pool to **every** caller of the router.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties is fully bypassed. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the restricted pool. The extension checks `allowedSwapper[pool][router]`; if the router is allowlisted, the swap proceeds regardless of who the real caller is. The attacker receives pool output tokens and the pool's LP balances are depleted by an unauthorized party, constituting a direct loss of LP principal.

### Likelihood Explanation

A pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address — there is no other mechanism. This is the expected operational path for any production pool that uses both the allowlist extension and the router. The bypass is therefore reachable on any such pool without any privileged action by the attacker.

### Recommendation

Pass the original user's address through the swap call so extensions can gate on the true initiator. One approach: add an explicit `originator` field to the pool's `swap` parameters (or to `extensionData`) that the router populates with `msg.sender` before calling the pool. `SwapAllowlistExtension.beforeSwap` should then check `originator` rather than `sender`. Alternatively, the extension can require that `sender == tx.origin` for EOA-only pools, though this breaks smart-contract integrations. The cleanest fix is a dedicated originator forwarding convention enforced by the router and consumed by the extension.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for their allowlisted users.
3. Attacker (address not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
4. The router calls `pool.swap(...)` — `msg.sender` at the pool is the router.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
6. The swap executes successfully for the attacker, bypassing the intended per-user gate.
7. LP assets leave the pool to an unauthorized counterparty. [6](#0-5) [7](#0-6)

### Citations

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
