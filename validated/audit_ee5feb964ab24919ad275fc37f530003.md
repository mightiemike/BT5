Looking at the actual code to validate each step of the claimed exploit chain.

The entire exploit chain is confirmed by the production code. Every step checks out:

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
2. `ExtensionCalling._beforeSwap()` forwards `sender` verbatim into the extension call [2](#0-1) 
3. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` — pool is `msg.sender`, router is `sender` when routed [3](#0-2) 
4. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly with no encoding of the original caller into `extensionData` [4](#0-3) 
5. `DepositAllowlistExtension` correctly checks `owner` (explicit depositor param), confirming the swap extension lacks an equivalent trusted-identity parameter [5](#0-4) 

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of original user, allowing any user to bypass per-user swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which equals `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address. A pool admin who allowlists the router to support standard periphery UX inadvertently grants every user the ability to bypass the per-user allowlist, defeating the extension's curation purpose entirely.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap` (MetricOmmPool.sol L230–231). `ExtensionCalling._beforeSwap()` forwards this value verbatim as `sender` to every configured extension (ExtensionCalling.sol L160–166). `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` (SwapAllowlistExtension.sol L37).

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly (MetricOmmSimpleRouter.sol L72–80) without encoding the original caller into `extensionData`. Inside `pool.swap()`, `msg.sender` is the router contract, so the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

No existing guard corrects this: the `swap()` interface has no explicit `swapper` parameter analogous to the `owner` parameter in `addLiquidity`. `DepositAllowlistExtension` correctly checks `owner` (the explicit depositor parameter, DepositAllowlistExtension.sol L38), which is always the actual depositor regardless of who calls `addLiquidity`. The swap extension has no equivalent trusted-identity field available to it.

## Impact Explanation
When a pool admin calls `setAllowedToSwap(pool, router, true)` to enable standard router-mediated swaps, `allowedSwapper[pool][router] == true`. Any unprivileged user can then call `router.exactInputSingle({pool: pool, ...})` and the extension passes unconditionally. The per-user allowlist ceases to function as a curation gate; disallowed users can freely trade against the curated pool's liquidity. This constitutes broken core extension functionality and an admin-boundary break reachable by any unprivileged trader.

## Likelihood Explanation
The trigger requires the pool admin to allowlist the router address. This is a natural and expected operational step for any curated pool that also wants to support the standard periphery UX. The admin has no on-chain signal that doing so collapses all per-user distinctions. Once the router is allowlisted, the bypass is reachable by any address with no further preconditions, making it Medium likelihood.

## Recommendation
The extension must check the economically relevant actor, not the immediate caller. Two options:

1. **Add an explicit `swapper` parameter to the swap interface** (analogous to `owner` in `addLiquidity`) that the pool populates from a trusted source, so the extension always receives the original user regardless of routing path.
2. **Router encodes original caller into `extensionData`**: the router encodes `msg.sender` into `extensionData` and the extension decodes and checks it. This requires a convention between router and extension and is weaker since it is opt-in per call.

Until fixed, pool admins must not allowlist the router address on pools that rely on `SwapAllowlistExtension` for per-user curation.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable standard router UX for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  - Router calls pool.swap(); msg.sender inside pool = router.
  - _beforeSwap passes sender = router to SwapAllowlistExtension.
  - Extension checks allowedSwapper[pool][router] == true → passes.
  - Attacker's swap executes against the curated pool's liquidity.

Expected: revert NotAllowedToSwap (attacker is not on the allowlist).
Actual:   swap succeeds; allowlist is bypassed.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-166)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
