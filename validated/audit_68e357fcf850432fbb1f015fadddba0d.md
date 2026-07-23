Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router — a necessary step to enable any router-mediated swap — every unprivileged user can bypass the allowlist by calling the router instead of the pool directly.

## Finding Description

**Verified call chain:**

1. `MetricOmmSimpleRouter.exactInputSingle()` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the pool's `msg.sender` is the router. [1](#0-0) 

2. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`. [2](#0-1) 

3. `ExtensionCalling._beforeSwap()` encodes and dispatches `IMetricOmmExtensions.beforeSwap(sender=router, ...)` to every configured extension, passing `sender` unchanged. [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` evaluates:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool and `sender` is the **router**. The check becomes `allowedSwapper[pool][router]`. [4](#0-3) 

If the pool admin has called `setAllowedToSwap(pool, router, true)` — the only way to enable router-mediated swaps for any allowlisted user — the condition passes for **every caller** of the router, regardless of whether that caller is on the allowlist. The router passes `params.extensionData` directly to the pool without encoding the real caller's identity, so the extension has no way to recover the end user. [5](#0-4) 

## Impact Explanation
`SwapAllowlistExtension` is the production mechanism for restricting which addresses may trade in a pool. Once the router is allowlisted (to support any router-mediated swap), the allowlist is completely ineffective: any address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and execute swaps that the pool admin intended to block. This breaks the core access-control invariant of the extension — the exact invariant identified in the "Allowlist path" audit pivot — and allows unauthorized parties to trade in pools restricted by KYC, whitelist, or rate-limiting intent. [6](#0-5) 

## Likelihood Explanation
The scenario is reachable whenever a pool admin: (1) deploys a pool with `SwapAllowlistExtension` to restrict swaps to specific addresses, and (2) calls `setAllowedToSwap(pool, router, true)` to let allowlisted users trade via the router. Step 2 is not a mistake in isolation — it is the only way to enable router usage — but it silently opens the gate to all users. No special privileges, flash loans, or oracle manipulation are required; any unprivileged user who discovers the router is allowlisted can immediately bypass the restriction. [7](#0-6) 

## Recommendation
`SwapAllowlistExtension.beforeSwap` should gate the end user, not the intermediary. The cleanest fix: the router encodes `msg.sender` into `extensionData`, and the extension decodes it when present, falling back to `sender` for direct pool calls. Alternatively, gate on `recipient` (the second argument already received by the extension) for the common case where the user is also the recipient. A third option is to document that the router must never be allowlisted and provide a per-user router wrapper, but this is operationally fragile. [8](#0-7) 

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // to enable router usage
  admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowlisted user
  bob is NOT allowlisted

Attack:
  bob calls router.exactInputSingle({
      pool: pool,
      tokenIn: token1,
      tokenOut: token0,
      zeroForOne: false,
      amountIn: X,
      recipient: bob,
      ...
  })

  pool.swap() is called with msg.sender = router
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  Swap executes — bob receives token0 despite not being allowlisted

Direct call for comparison:
  bob calls pool.swap(...) directly
  _beforeSwap(sender=bob, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][bob] == false  ✗
  Reverts with NotAllowedToSwap  ✓ (guard works correctly for direct calls)
``` [9](#0-8)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
