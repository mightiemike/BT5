Audit Report

## Title
`SwapAllowlistExtension` Per-User Allowlist Bypassed via Router Address Substitution - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` — the direct caller of `pool.swap`. When `MetricOmmSimpleRouter` is used, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router (a necessary step for approved users to trade through it) inadvertently grants every unprivileged address the ability to bypass the per-user gate by routing through the router.

## Finding Description
`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is the value forwarded above. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

At that point the pool's `msg.sender` is the router. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The end user's identity is never consulted. The router stores the real payer in transient storage (`_setNextCallbackContext`) for the payment callback only — it is never surfaced to the extension layer. No existing guard in `SwapAllowlistExtension`, `ExtensionCalling`, or `MetricOmmPool` recovers the original caller.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` is designed so that only explicitly approved counterparties can trade against LP liquidity at oracle-derived prices. Once the router is allowlisted, that boundary collapses entirely: any unprivileged EOA can execute swaps against the LP's full bin liquidity. The LP suffers direct loss of principal through trades they never intended to permit. This constitutes a direct loss of user principal and a broken core pool access-control invariant, meeting the contest's Critical/High threshold.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery entry point. Any pool admin who wants their approved users to trade through the router must allowlist it — this is a natural and expected operational state. The bypass requires no special privilege, no flash loan, and no unusual token behavior. A single `exactInputSingle` call from any EOA is sufficient. The condition is not edge-case; it is the normal production configuration for router-enabled curated pools.

## Recommendation
The extension must gate the economic actor, not the intermediary. The recommended fix is a trusted-forwarder pattern: the router appends the original `msg.sender` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` matches a registry of trusted routers per pool. For non-router callers, it falls back to checking `sender` directly. A complementary measure is to document (and optionally enforce on-chain) that allowlisting a known periphery contract is equivalent to `allowAllSwappers = true`.

## Proof of Concept
```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as EXTENSION_1, beforeSwap order = 1.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack
──────
4. bob (not on allowlist) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool:          pool,
           recipient:     bob,
           zeroForOne:    true,
           amountIn:      X,
           extensionData: ""
       })

5. Router calls pool.swap(bob, true, X, ..., "")
   → pool's msg.sender = router
   → _beforeSwap(sender=router, ...)
   → SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true ✓
   → swap executes; bob receives output tokens

Result: bob, who is not on the allowlist, successfully swaps against LP liquidity.
        The allowlist invariant is broken; LP funds are exposed to unauthorized counterparties.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
