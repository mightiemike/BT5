Based on the code I was able to read, here is the analysis:

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User — Allowlist Fully Bypassed via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument the pool passes into the hook. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the pool forwards the router address as `sender`. The extension then checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any non-allowlisted user can bypass a curated pool's swap gate by routing through the public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct — the pool calls the extension) and `sender` is the first argument the pool passes into the hook, which is `msg.sender` of the pool's own `swap` call — i.e., the router.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no explicit `sender` override:

```solidity
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
``` [2](#0-1) 

The original caller (`msg.sender`) is stored only in transient callback context for payment purposes — it is never forwarded to the pool as the swap `sender`. The pool therefore sees `msg.sender = router` and passes the router address as `sender` to every extension hook.

For multihop `exactInput`, intermediate hops compound the problem: the payer is explicitly set to `address(this)` (the router) for hops after the first:

```solidity
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
``` [3](#0-2) 

In every case the extension sees `sender = router`, never the real user.

This creates an irreconcilable dilemma for any pool admin who configures `SwapAllowlistExtension`:

- **Router not allowlisted** → all router-mediated swaps revert for every user, including allowlisted ones. The router is unusable on that pool.
- **Router allowlisted** → every user, including non-allowlisted ones, can bypass the gate by routing through the public router.

There is no configuration that simultaneously supports the router and enforces per-user allowlist policy.

### Impact Explanation

A curated pool (e.g., KYC-gated, institution-only, or regulatory-restricted) that configures `SwapAllowlistExtension` to gate individual swappers can be fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The unauthorized user trades on the restricted pool, receiving output tokens they should not be entitled to. This is a direct policy bypass with fund-flow consequences on every allowlisted pool that also supports the router.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint documented for end users. Any pool admin who deploys `SwapAllowlistExtension` and also expects users to use the router (the normal case) will either break the router for all users or inadvertently open the allowlist to everyone. The bypass requires no special privilege — any EOA can call the router.

### Recommendation

The pool must forward the original caller's identity to the extension, not its own `msg.sender`. Two standard approaches:

1. **Pass the original sender explicitly**: Add a `sender` parameter to the pool's `swap` function (or derive it from a trusted transient slot set by the router before the call) and forward it as the first argument to `beforeSwap`. The router already stores `msg.sender` in transient storage via `_setNextCallbackContext`; the pool should read that slot and pass it to the extension.

2. **Check `tx.origin` as a fallback** (weaker, not recommended for general use): Only acceptable in contexts where `tx.origin` is a reliable proxy for the economic actor, which is not always the case.

The cleanest fix is for the pool to accept an explicit `sender` address in its `swap` call (similar to how Uniswap v4 passes `msg.sender` through the unlock/callback chain) so that the extension always sees the true initiating user regardless of routing depth.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users (or is forced to do so to make the router work at all).
3. Non-allowlisted attacker calls `router.exactInputSingle(...)` targeting the restricted pool.
4. Pool calls `extension.beforeSwap(router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
6. Attacker receives output tokens from the restricted pool without being individually allowlisted.

The same path applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`. [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-103)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
```
