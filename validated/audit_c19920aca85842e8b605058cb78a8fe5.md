Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the end user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is exactly `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is used, `sender` is the router's address, not the end user's. If the pool admin allowlists the router — the only way to let legitimate users trade through it — every unprivileged caller can bypass the per-user gate. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing so.

## Finding Description

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the caller of the extension) and `sender` is the first argument passed by the pool: [1](#0-0) 

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [3](#0-2) 

Therefore, the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. This creates an irresolvable dilemma:

| Admin action | Effect |
|---|---|
| Allowlist individual users only | Those users **cannot** swap through the router (router not allowlisted → revert) |
| Allowlist the router address | **All** users can swap through the router, bypassing the per-user gate |

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the position owner explicitly passed by the caller), which is independent of any intermediary: [4](#0-3) 

The swap path has no equivalent mechanism to propagate the real end user's identity through the router to the extension.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC'd addresses, protocol-controlled addresses, or specific market makers) is fully bypassable by any unprivileged user routing through `MetricOmmSimpleRouter`. Once the router is allowlisted — which is the only way to let legitimate users trade — the allowlist provides zero per-user protection. Non-allowlisted users can trade against the pool's LPs, causing adverse selection losses that LP depositors did not consent to and the pool admin intended to prevent. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's intended access control is rendered ineffective by an unprivileged call path.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who deploy `SwapAllowlistExtension` will naturally allowlist the router to let their intended users trade. Once they do, the bypass is open to any caller. No privileged access, special token behavior, or unusual setup is required — only a call to the public `exactInputSingle` (or `exactOutputSingle`, `exactInput`, `exactOutput`) function on the router.

## Recommendation

The extension must gate on the actual end user, not the immediate caller. The simplest correct fix is to have the router encode the real payer/user in `extensionData` and have the extension decode and gate on that address when `sender` is a known router. Specifically:

1. **Router-side**: In `exactInputSingle` (and other swap entry points), encode `msg.sender` into `params.extensionData` before passing it to `pool.swap()`.
2. **Extension-side**: In `beforeSwap`, if `sender` is a registered trusted router, decode the real user from `extensionData` and check `allowedSwapper[pool][realUser]`; otherwise check `allowedSwapper[pool][sender]` as today.

A trusted-router registry controlled by the extension admin is required to prevent spoofing of `extensionData` by untrusted callers.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension on beforeSwap
  admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
  admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  pool.swap() is called with msg.sender = router
  _beforeSwap(sender=router, ...)
  extension checks: allowedSwapper[pool][router] == true  → passes
  bob's swap executes successfully despite not being allowlisted

DoS alternative (admin does NOT allowlist the router):
  alice (allowlisted) calls router.exactInputSingle(...)
  extension checks: allowedSwapper[pool][router] == false → NotAllowedToSwap
  alice cannot use the router even though she is individually allowlisted
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` as `beforeSwap` hook.
2. Call `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. Prank as `bob` (not allowlisted) and call `router.exactInputSingle(...)`.
4. Assert the swap succeeds — demonstrating the bypass.
5. Alternatively, remove the router allowlist and assert alice's router call reverts with `NotAllowedToSwap`.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
