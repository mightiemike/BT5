Audit Report

## Title
Router-Mediated Swaps Replace End-User Identity with Router Address in `SwapAllowlistExtension.beforeSwap`, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
When a swap is routed through `MetricOmmSimpleRouter`, the pool receives the router contract as `msg.sender` and forwards it as the `sender` argument to `SwapAllowlistExtension.beforeSwap`. The extension then evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any unprivileged user can bypass a per-user allowlist by routing through the router if the router is allowlisted, completely defeating the access-control invariant the pool admin configured.

## Finding Description

**Step 1 — Router calls the pool directly as `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly with no intermediary. The pool therefore sees `msg.sender` = router contract address, not the originating EOA. [1](#0-0) 

**Step 2 — Pool passes `msg.sender` (= router) as `sender` to the hook:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, propagating the router address as the `sender` argument. [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks the router, not the user:**

The extension receives `sender` = router address and evaluates `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]`. The originating user's address is never inspected. [3](#0-2) 

The allowlist mapping is keyed `allowedSwapper[pool][swapper]` and is set only by `onlyPoolAdmin`. There is no mechanism in the router or pool to propagate the originating user's address into the `sender` slot of the hook call. [4](#0-3) [5](#0-4) 

## Impact Explanation
The swap allowlist is a core pool access-control mechanism. When the router is allowlisted (a natural and expected configuration so that KYC'd users can trade via the standard periphery), the allowlist gate is rendered completely ineffective for all router-mediated swaps. Any unprivileged user can trade in a curated pool they were never authorized to access. This constitutes broken core pool functionality: the access-control invariant configured by the pool admin is fully defeated for the router path, which is the primary trading entrypoint.

## Likelihood Explanation
The router is the standard, documented periphery entrypoint. Any pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router — a natural and expected configuration for any pool that wants KYC'd users to trade via periphery — is fully exposed. No privileged access, malicious setup, or special conditions are required. Any user can exploit this by simply calling the public router functions (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

## Recommendation
The router should encode `msg.sender` (the originating user) into `extensionData` on every swap call. `SwapAllowlistExtension.beforeSwap` should decode and use this value when the direct `sender` is a known router, or the pool's hook interface should carry a separate `origin` field distinct from `sender` (the immediate caller), passed explicitly and verifiably through the call chain. Alternatively, the extension can maintain a router registry and, when `sender` is a registered router, require that the originating user's address be ABI-encoded in `extensionData` and verified there.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the router so KYC'd users can trade via periphery.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` — alice is the only intended user.
4. Bob (not allowlisted, `allowedSwapper[pool][bob] == false`) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Bob successfully swaps in a pool he was never authorized to access, with `allowedSwapper[pool][bob] == false` throughout.

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
