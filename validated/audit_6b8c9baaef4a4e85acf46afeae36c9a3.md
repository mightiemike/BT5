Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual swapper, enabling full allowlist bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap` call — the direct caller, not the end user. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address. A pool admin who allowlists the router to restore router-mediated access inadvertently opens the pool to every address, completely defeating the allowlist. Even without that admin action, allowlisted users cannot swap through the router at all.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` (its direct caller) as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to the extension. `SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

So inside `pool.swap`, `msg.sender` is the **router address**. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores the first `sender` parameter and checks `owner` — the actual LP position owner explicitly supplied by the caller — which survives the router hop unchanged: [4](#0-3) 

The swap extension has no equivalent explicit-user field; it relies solely on `sender`, which collapses to the router address on every router-mediated swap.

## Impact Explanation
**Scenario A — Allowlist bypass (High):** A pool deployed with `SwapAllowlistExtension` to restrict trading to KYC'd addresses will reject allowlisted users who try to swap through the router (router not in allowlist). The pool admin, attempting to fix this, calls `setAllowedToSwap(pool, router, true)`. From that moment, any address can bypass the allowlist by calling `router.exactInputSingle`, because the extension only checks `allowedSwapper[pool][router]` which is now `true`. Unauthorized users can trade on a pool explicitly restricted to them.

**Scenario B — Broken core swap functionality (High):** Even without the bypass, allowlisted users cannot use the standard periphery router. They must call `pool.swap` directly and implement `IMetricOmmSwapCallback` themselves, which is not the intended user flow and makes the pool effectively unusable for normal users.

Both impacts are direct and fund-relevant: Scenario A is an allowlist bypass permitting unauthorized trading; Scenario B breaks the core swap flow for legitimate users.

## Likelihood Explanation
The router is the primary and documented swap entrypoint for end users. Any pool that deploys `SwapAllowlistExtension` and expects users to swap through the router will encounter Scenario B immediately. The bypass path (Scenario A) requires only that the pool admin allowlists the router — a natural and predictable remediation attempt for Scenario B — making the full bypass reachable through a single admin action. No special privileges, flash loans, or exotic token behavior are required.

## Recommendation
The `beforeSwap` hook must check the economically relevant actor, not the intermediary. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Mirror the deposit pattern**: Add an explicit `swapper` parameter to the pool's `swap` signature (analogous to `owner` in `addLiquidity`) so the actual user identity survives the router hop and is forwarded to the extension unchanged. This aligns `SwapAllowlistExtension` with the already-correct `DepositAllowlistExtension` design.

## Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][router] = false

Step 1 — Broken flow:
  alice calls router.exactInputSingle({pool, ...})
  → router calls pool.swap(recipient, ...)
  → pool passes msg.sender (= router) as sender to _beforeSwap
  → extension checks allowedSwapper[pool][router] == false → REVERT
  alice cannot swap through the router despite being allowlisted.

Step 2 — Admin "fix":
  pool admin calls extension.setAllowedToSwap(pool, router, true)
  allowedSwapper[pool][router] = true

Step 3 — Bypass:
  bob (not allowlisted) calls router.exactInputSingle({pool, ...})
  → router calls pool.swap(recipient, ...)
  → pool passes msg.sender (= router) as sender to _beforeSwap
  → extension checks allowedSwapper[pool][router] == true → PASSES
  bob swaps successfully on a pool that was supposed to block him.
```

Root cause: `SwapAllowlistExtension.beforeSwap` at line 37 checks `sender` (the router) instead of the actual end user. [5](#0-4)

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
