### Title
SwapAllowlistExtension Gates on Router Address Instead of Economic Actor, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, the router becomes `sender`. A pool admin who allowlists the router to enable router-based swaps for legitimate users inadvertently opens the gate to every user, because the router is a single address: either it is allowlisted (all users can use it) or it is not (no user can use it through the router). The individual-user allowlist is therefore unenforceable for any router-mediated swap.

### Finding Description

`SwapAllowlistExtension.beforeSwap` overrides `BaseMetricExtension.beforeSwap` and checks the `sender` argument:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is whatever `msg.sender` was when `pool.swap` was called. The pool always forwards its own `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient, ...
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

So the extension sees `sender = router`, not the originating user. The allowlist check becomes `allowedSwapper[pool][router]`.

Contrast this with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly gates on `owner` (the economic beneficiary of the position), not `sender` (the payer/caller). The swap extension applies the analogous check to the wrong actor.

A pool admin who wants legitimate allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the guard passes for every user who routes through it — including users who were never individually allowlisted.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., to prevent toxic order flow and protect LP value) loses that protection for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against the pool, exposing LPs to the exact adverse-selection risk the allowlist was meant to prevent. This is a direct loss of LP assets above Sherlock thresholds when the pool carries meaningful liquidity.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard supported periphery swap path. A pool admin who allowlists specific users and also wants those users to benefit from multi-hop routing, slippage protection, or deadline enforcement will naturally allowlist the router. The admin has no in-protocol signal that doing so opens the gate to all users. The bypass requires no special privilege — any address can call `MetricOmmSimpleRouter.exactInputSingle`.

### Recommendation

Gate the swap allowlist on the economic actor, not the immediate caller. Two options:

1. **Check `recipient` instead of `sender`**: The recipient is the address that receives output tokens and is the closest on-chain proxy for the economic actor in a single-hop swap. This is consistent with how `DepositAllowlistExtension` gates on `owner`.

2. **Require the router to forward the originating user**: Add an optional `address originator` field to `extensionData` that the router populates with `msg.sender`, and have the extension verify it (e.g., with a signature or by trusting a registry of approved routers that attest the originator).

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — Alice is a trusted counterparty.
3. Admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use the router for multi-hop or slippage-protected swaps.
4. Bob (never allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(bobRecipient, ...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(router, bobRecipient, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → guard passes.
8. Bob's swap executes against the curated pool. The individual allowlist is fully bypassed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L229-241)
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
