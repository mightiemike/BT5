Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing any user to bypass per-pool swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently grants unrestricted swap access to every user, rendering the allowlist ineffective.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct mapping key) and `sender` is the address passed by the pool as the swap initiator. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // sender = whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle`, the originating user's address is stored in transient storage only for the payment callback, but the pool is called directly with the router as `msg.sender`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

The `extensionData` field passed through the router is forwarded to the extension but the `bytes calldata` parameter in `beforeSwap` is unnamed and unused — there is no existing mechanism to recover the originating user's identity inside the extension. [5](#0-4) 

This creates an irreconcilable conflict: if the admin does not allowlist the router, allowlisted users cannot use the router at all. If the admin does allowlist the router (the natural step to enable router-mediated swaps), every user — including non-allowlisted ones — can bypass the allowlist by routing through the router, because the extension sees `allowedSwapper[pool][router] = true`.

## Impact Explanation
Any unprivileged user can bypass a pool's `SwapAllowlistExtension` by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on a pool that has allowlisted the router. The allowlist — the pool admin's primary mechanism for restricting access (e.g., KYC, institutional-only pools, regulatory compliance) — is rendered completely ineffective. Non-allowlisted users can execute swaps at oracle-derived prices on a pool intended to be restricted, violating the pool's access policy and potentially draining LP funds. This constitutes a broken core pool functionality (access control) causing potential loss of funds and an admin-boundary break where an unprivileged path bypasses the pool admin's intended restriction.

## Likelihood Explanation
The pool admin must allowlist the router for router-mediated swaps to work at all — this is a natural and expected configuration step for any production pool supporting the standard periphery. The admin is unlikely to realize that allowlisting the router grants unrestricted access to all users, since the extension's NatSpec states it "Gates `swap` by swapper address, per pool" — implying user-level granularity. [6](#0-5) 

The bypass requires only a standard router call, which is the most common user-facing entry point. No special privileges, flash loans, or complex setup are required — any EOA can exploit this.

## Recommendation
The `SwapAllowlistExtension` must check the originating user's address, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pool-level fix:** The pool passes the originating user's address as a dedicated parameter to the extension hook (separate from `sender`, which is the immediate caller). The hook signature carries both the immediate caller and the economic actor.
2. **Extension-level fix:** The router encodes the originating user's address in `extensionData`; the extension decodes and verifies it, while also verifying that `msg.sender` (the pool's caller) is a trusted router registered with the factory. This prevents spoofing while preserving user-level granularity.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps. [7](#0-6) 
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router. [8](#0-7) 
6. Pool calls `_beforeSwap(router, ...)` — `sender` = router. [9](#0-8) 
7. Extension checks `allowedSwapper[pool][router]` = `true` → passes without revert. [1](#0-0) 
8. Attacker's swap executes on the restricted pool, bypassing the allowlist entirely.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
