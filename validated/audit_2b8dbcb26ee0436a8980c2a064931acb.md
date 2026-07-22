### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the end user, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is intended to gate pool swaps by swapper address. However, the `sender` argument it receives and checks is `msg.sender` of `pool.swap()` — which is the **router contract**, not the end user. When a pool admin configures per-user restrictions and allowlists the router to enable normal swap flows, the guard is completely bypassed for every user who routes through `MetricOmmSimpleRouter`.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to the extension: [1](#0-0) 

`_beforeSwap` encodes it as the first argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`: [4](#0-3) 

So `sender` received by the extension is the **router address**, not the end user (`msg.sender` of `exactInputSingle`). The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the actual position owner passed explicitly by the caller): [5](#0-4) 

This asymmetry means the deposit guard correctly enforces per-user access while the swap guard does not.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to specific users faces an impossible choice:

1. **Allowlist the router** (the natural fix to allow normal swap flows) → every user who calls through the router bypasses the per-user restriction entirely. The allowlist guard is rendered inoperative.
2. **Do not allowlist the router** → all allowlisted users are blocked from swapping through `MetricOmmSimpleRouter`, making the standard swap path unusable for the pool.

In either case the invariant "only allowlisted addresses may swap" is broken. Any non-allowlisted user can execute swaps against a restricted pool simply by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router, draining LP assets at oracle-derived prices without authorization.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard production swap path. Any pool that configures `SwapAllowlistExtension` for per-user access control and also needs to support the router (the common case) will exhibit this bypass. No special privileges or malicious setup are required — any ordinary user can trigger it by calling the router.

---

### Recommendation

Pass the end user's address through the swap call so the extension can check it. Two options:

1. **Preferred — add a `swapper` field to `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires no core changes.

2. **Core change — add a `swapper` parameter to `pool.swap()`**: The pool accepts an explicit `swapper` address (validated against `msg.sender` or a trusted router registry) and forwards it to extensions as a dedicated argument instead of reusing `sender`.

Additionally, update `SwapAllowlistExtension.beforeSwap` to check the decoded end-user address rather than the raw `sender` argument.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // must allowlist router for normal flow
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  - Alice (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(...)
  - Router calls pool.swap(recipient, ...)  →  msg.sender = router
  - Pool calls _beforeSwap(router, ...)
  - Extension checks allowedSwapper[pool][router] == true  →  PASSES
  - Alice's swap executes against the restricted pool.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds — per-user restriction bypassed
``` [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
