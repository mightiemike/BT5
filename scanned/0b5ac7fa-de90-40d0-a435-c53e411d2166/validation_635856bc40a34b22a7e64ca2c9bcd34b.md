### Title
`SwapAllowlistExtension` gates by router address instead of actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the actual user's address. If the router is allowlisted — which is required for any router-mediated swap to work for allowlisted users — any non-allowlisted user can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the first parameter passed by the pool — which is `msg.sender` of the `pool.swap()` call, i.e., the **direct caller of the pool**. [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle()` is called:

1. Router calls `pool.swap(recipient, ..., extensionData)` with `msg.sender = router`
2. Pool calls `_beforeSwap(msg.sender=router, recipient, ...)` — passing the router as `sender` [2](#0-1) 

3. `ExtensionCalling._beforeSwap` encodes `sender` (the router) and dispatches to the extension [3](#0-2) 

4. Extension's `beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` — **not** `allowedSwapper[pool][user]` [4](#0-3) 

The router passes `extensionData` through unmodified and does not inject the actual user's address anywhere the extension can read it: [5](#0-4) 

This creates an irresolvable conflict for the pool admin:

- To allow allowlisted users to swap through the router → must allowlist the router
- Once the router is allowlisted → **every** user can swap through the router, bypassing per-user restrictions

Contrast with `DepositAllowlistExtension`, which correctly ignores `sender` and checks `owner` (the position owner), the economically relevant actor: [6](#0-5) 

The swap extension has no equivalent correct binding.

---

### Impact Explanation

Any non-allowlisted user can bypass the swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle()`, `exactInput()`, `exactOutputSingle()`, or `exactOutput()` against a pool that has `SwapAllowlistExtension` configured with the router allowlisted. This allows unauthorized swaps against LP liquidity in pools intended to be restricted (e.g., private institutional pools, KYC-gated pools). LPs suffer adverse selection from unauthorized counterparties they did not intend to trade with — a direct loss of owed LP assets and pool solvency risk from unintended exposure.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router, which is a necessary step for any allowlisted user to swap through the router. Any pool that uses `SwapAllowlistExtension` and wants to support router-mediated swaps for allowlisted users is vulnerable. The attacker only needs to call the router with the target pool address — no special privileges, no malicious setup.

---

### Recommendation

1. **Correct fix**: The extension should decode the actual user's address from `extensionData`. The router should be modified to prepend `msg.sender` (the actual user) to `extensionData` before forwarding to the pool, and the extension should decode and verify it against the allowlist.
2. **Alternative**: Add a separate `allowedRouter` mapping so that the extension can distinguish between "router is allowed to relay for any user" and "specific user is allowed." The extension would then need the router to pass the user identity in `extensionData`.
3. **Minimum**: Document explicitly that `SwapAllowlistExtension` is incompatible with router-mediated swaps and must only be used with direct `pool.swap()` calls.

---

### Proof of Concept

```
Setup:
  - Pool P has SwapAllowlistExtension E configured
  - Pool admin allowlists user A (KYC'd): allowedSwapper[P][A] = true
  - Pool admin allowlists the router R (so A can use the router): allowedSwapper[P][R] = true

Attack:
  1. Non-allowlisted user B calls:
       router.exactInputSingle({pool: P, recipient: B, amountIn: X, ...})

  2. Router calls:
       P.swap(B, zeroForOne, amountIn, priceLimitX64, "", extensionData)
       // msg.sender = router R

  3. Pool calls:
       _beforeSwap(sender=R, recipient=B, ...)

  4. Extension checks:
       allowedSwapper[P][R] == true  →  no revert

  5. Swap executes. User B, not allowlisted, successfully swaps in the restricted pool.
``` [1](#0-0) [2](#0-1) [7](#0-6)

### Citations

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
