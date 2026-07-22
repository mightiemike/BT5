### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address (the natural action to let users use the standard periphery), every unprivileged user can bypass the swap allowlist by routing through the router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(...)` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. The router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)`.
   - At this point, `msg.sender` inside `MetricOmmPool.swap` is the **router address**.
3. The pool calls `_beforeSwap(msg.sender, ...)`, passing the router address as `sender`. [1](#0-0) 

4. `ExtensionCalling._beforeSwap` encodes `sender` (= router) as the first argument to the extension hook. [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap` receives `sender = router` and evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The allowlist lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**The bypass:** A pool admin who wants allowlisted users to be able to use the standard router must call `setAllowedToSwap(pool, router, true)`. Once the router address is allowlisted, **any** user — including those not individually allowlisted — can call any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension will see `sender = router`, which passes the check. The end user's identity is never inspected. [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd counterparties, whitelisted market makers, or private participants) loses all enforcement the moment the router is allowlisted. Any unprivileged user can trade on the pool by routing through `MetricOmmSimpleRouter`. This is a complete allowlist bypass with direct fund-impact consequences: unauthorized users can drain liquidity, extract value at oracle-anchored prices, or interact with a pool that was contractually restricted to specific parties.

---

### Likelihood Explanation

The likelihood is **medium-high**. Allowlisting the router is the natural and expected action for any pool admin who wants their allowlisted users to be able to use the standard periphery interface rather than calling the pool directly. The router is a first-party, factory-validated contract. There is no documentation or on-chain warning that allowlisting the router opens the gate to all users. A pool admin following normal integration patterns will trigger this bypass without realizing it.

---

### Recommendation

The `beforeSwap` hook should gate on the **end user's identity**, not the immediate pool caller. Two options:

1. **Pass the original initiator through `extensionData`:** The router encodes `msg.sender` (the end user) into `extensionData`, and the extension decodes and checks it. This requires a protocol-level convention.

2. **Check `recipient` instead of `sender` (partial fix):** For single-hop swaps the recipient is often the end user, but this is not reliable for multi-hop paths.

3. **Preferred — mirror the deposit allowlist pattern:** `DepositAllowlistExtension` correctly gates on `owner` (the position owner), not `sender` (the payer/caller). The swap allowlist should similarly accept an explicit `swapper` identity argument that the router populates from its own `msg.sender` via `extensionData`, and the extension should decode and verify it. [5](#0-4) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=bob, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes on the curated pool despite not being allowlisted

Expected: NotAllowedToSwap() revert for bob.
Actual:   Swap succeeds; bob trades on a pool restricted to allowlisted users only.
```

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with `msg.sender = router`. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
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
