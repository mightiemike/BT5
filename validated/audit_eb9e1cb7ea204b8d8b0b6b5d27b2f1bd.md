Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Enabling Allowlist Bypass or Blocking Allowlisted Users — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against `allowedSwapper`, but when users interact through `MetricOmmSimpleRouter`, `sender` is the router address — not the actual user. This creates two mutually exclusive failure modes: allowlisting the router bypasses per-user curation entirely, while not allowlisting it silently blocks individually-allowlisted users from using the router.

## Finding Description

In `SwapAllowlistExtension.beforeSwap`, the check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the pool calls the extension), and `sender` is whatever `msg.sender` the pool received when `swap()` was called.

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` in `ExtensionCalling.sol` forwards this `sender` directly to the extension: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly — so `msg.sender` of the pool's `swap()` call is the router contract, not the user: [3](#0-2) 

The call chain is:
```
User → Router.exactInputSingle()
       Router → Pool.swap()   [msg.sender = router]
                Pool → Extension.beforeSwap(sender = router, ...)
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the actual depositor), not `sender` (the intermediary): [4](#0-3) 

This asymmetry confirms the design intent is to gate by the actual user, but `SwapAllowlistExtension` implements it incorrectly for the swap path. [5](#0-4) 

## Impact Explanation

**Failure mode A — Allowlist bypass (High):** A pool admin configures `SwapAllowlistExtension` to restrict swaps to specific addresses and allowlists the router to enable router-based swaps. Because the extension checks `allowedSwapper[pool][router]`, every user who calls the router passes the check regardless of individual allowlist status. The entire per-user curation is bypassed for any user routing through `MetricOmmSimpleRouter`. This is a direct admin-boundary break: an unprivileged, non-allowlisted trader can swap in a curated pool.

**Failure mode B — Broken core functionality (Medium):** If the pool admin does not allowlist the router, individually allowlisted users who call the router are blocked with `NotAllowedToSwap`, even though they are permitted by the allowlist. The router — the primary user-facing swap interface — becomes unusable for curated pools, constituting broken core swap functionality.

## Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects users to interact via `MetricOmmSimpleRouter` is affected. The pool admin faces an impossible choice: allowlist the router (bypass) or don't (broken router access). No configuration of the current extension resolves both failure modes simultaneously. This affects all curated pools using the standard periphery router, making likelihood medium-high.

## Recommendation

Pass the actual end-user identity through the hook. Two options:

1. **Add a `swapper` field to `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool, and `SwapAllowlistExtension` decodes and checks it. This mirrors the convention between router and extension.

2. **Check `recipient` instead of `sender`**: The `recipient` parameter (second argument to `beforeSwap`) is already set to the actual user by the router. However, this changes the semantic from "who initiates the swap" to "who receives output."

The cleanest fix is option 1, mirroring how `DepositAllowlistExtension` uses `owner` (the actual depositor) rather than `sender` (the liquidity adder).

## Proof of Concept

**Failure mode A (bypass):**
1. Deploy a pool with `SwapAllowlistExtension` configured as `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the router so router-based swaps work.
3. A non-allowlisted user `alice` calls `router.exactInputSingle(...)`.
4. The router calls `pool.swap(...)` with `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Alice, who was never individually allowlisted, successfully swaps in the curated pool.

**Failure mode B (blocked):**
1. Pool admin calls `setAllowedToSwap(pool, alice, true)` but does NOT allowlist the router.
2. Alice calls `router.exactInputSingle(...)`.
3. Extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap` revert.
4. Alice, who is individually allowlisted, cannot use the router at all.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
