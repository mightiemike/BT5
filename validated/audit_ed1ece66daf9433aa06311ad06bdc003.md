Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, enabling full per-user allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender = msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the actual user. A pool admin who allowlists the router address to support standard periphery usage inadvertently grants every unprivileged user the ability to bypass per-user allowlist enforcement entirely.

## Finding Description
**Pool passes `msg.sender` (the router) as `sender` to extensions:**

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**Router stores the actual user only in transient storage for the payment callback — never forwarded to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` stores `msg.sender` via `_setNextCallbackContext` for payment purposes, then calls `pool.swap(params.recipient, ...)` with the router as `msg.sender` to the pool: [3](#0-2) 

**Extension checks the router address, not the actual user:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the router address in the router path: [4](#0-3) 

**Contrast with `DepositAllowlistExtension`**, which correctly ignores `sender` (the immediate caller) and checks `owner` — the actual position owner explicitly passed through `addLiquidity`: [5](#0-4) 

The deposit path has an explicit `owner` parameter that survives the router/adder hop. The swap path has no equivalent; the only identity available to the extension is `msg.sender` of the pool call, which collapses to the router address for all router callers.

## Impact Explanation
A pool admin deploying a curated pool with `SwapAllowlistExtension` and allowlisting the router (a natural operational step to let allowlisted users trade via the standard periphery) inadvertently opens the pool to **all** users. The check `allowedSwapper[pool][router] == true` passes for every caller of the router regardless of individual allowlist status. Non-allowlisted users can execute swaps against the pool's liquidity, violating the curation policy. This constitutes broken core pool functionality — the allowlist extension's enforcement is completely nullified through the standard periphery path.

## Likelihood Explanation
The trigger is a pool admin allowlisting the router address, which is a reasonable and expected operational step for any curated pool that wants to support the standard periphery. The admin has no on-chain signal that doing so collapses per-user enforcement to a single binary gate. Any non-allowlisted user can exploit this by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` with no special privileges. The condition is reachable by any unprivileged trader.

## Recommendation
1. **Pass the actual user through the swap path.** Add an explicit `swapper` parameter to `pool.swap()` (analogous to `owner` in `addLiquidity`) and have the router forward `msg.sender` in that field. The extension then checks `allowedSwapper[pool][swapper]`.
2. **Alternatively**, have the router encode the actual user in `extensionData` under a well-known ABI layout and have `SwapAllowlistExtension` decode and verify it — though this is weaker because it relies on the router being the only entry point.
3. **At minimum**, document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and that allowlisting the router disables per-user enforcement.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][userA]  = true   // intended: only userA may swap
  allowedSwapper[pool][router] = true   // admin adds this to let userA use the router

Attack:
  userB (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool:      pool,
      recipient: userB,
      ...
    })

  Router executes (MetricOmmSimpleRouter.sol L72-80):
    pool.swap(userB, ...)   // msg.sender to pool = router

  Pool calls (MetricOmmPool.sol L230-240):
    _beforeSwap(sender=router, ...)

  SwapAllowlistExtension.beforeSwap (L37):
    allowedSwapper[pool][router] == true  →  passes, no revert

  userB receives output tokens from the curated pool.
  Per-user allowlist is completely bypassed.
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
