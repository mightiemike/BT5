Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Trader, Allowing Any User to Bypass the Curated-Pool Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument forwarded by the pool, which is the pool's own `msg.sender` at the time `swap` is called. When `MetricOmmSimpleRouter` intermediates the swap, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any unprivileged user can bypass a curated-pool allowlist by routing through the public router once the pool admin allowlists the router to enable router-mediated swaps.

## Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap(...)
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When the call path is `User → MetricOmmSimpleRouter.exactInputSingle → pool.swap(...)`, the pool's `msg.sender` is the router, so `sender = address(router)`. The extension evaluates `allowedSwapper[pool][router]`, never seeing the actual trader's address.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to inject the original `msg.sender` into the `sender` slot:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The `DepositAllowlistExtension` does not share this flaw because `beforeAddLiquidity` receives a separate `owner` parameter (the actual LP owner set by the caller), and the extension checks `owner` rather than `sender`. The swap interface has no equivalent `owner`-style parameter — only `sender` (the intermediary) and `recipient` (the output destination).

## Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to KYC-verified counterparties, protocol-owned bots, or whitelisted market makers provides no real restriction once the router is allowlisted. Any arbitrary address can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and execute swaps against the curated pool at oracle-derived prices. This constitutes a direct bypass of the core pool access-control mechanism the allowlist was designed to enforce, enabling unauthorized traders to drain LP liquidity — a direct loss of LP principal and broken core pool functionality.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary production entry point for swaps. Any pool admin who deploys `SwapAllowlistExtension` and wants users to swap via the router must allowlist the router — the natural operational step — which immediately opens the bypass to all users. No special privilege is required: any unprivileged address can call the public router functions. The bypass is repeatable and unconditional once the router is allowlisted.

## Recommendation

The pool's `swap` entrypoint should accept an explicit `onBehalfOf` parameter that trusted periphery contracts populate with the actual user address, and `SwapAllowlistExtension.beforeSwap` should check that field. Alternatively, the router should encode the actual user address in `extensionData`, and the extension should decode and verify it when `msg.sender` (the pool's caller) is a known trusted router. Using `tx.origin` is a simpler but weaker alternative that breaks for smart-contract wallets and introduces phishing risk.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps; does **not** allowlist `attacker`.
3. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(...)` — pool's `msg.sender` is the router.
5. Pool calls `extension.beforeSwap(router, ...)` — `sender = address(router)`.
6. `allowedSwapper[pool][router] == true` → check passes.
7. Attacker's swap executes against the curated pool, bypassing the intended allowlist gate.

The invariant "only addresses in `allowedSwapper[pool]` may trade" is broken for every router-mediated swap.

---

**Code references:**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `sender` (the router) instead of the actual user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` with the router as `msg.sender`, no user address forwarded: [4](#0-3) 

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (not `sender`), showing the asymmetry: [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
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
