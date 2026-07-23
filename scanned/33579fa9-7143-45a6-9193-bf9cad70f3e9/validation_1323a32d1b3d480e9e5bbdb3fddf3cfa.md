### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` from the pool's perspective. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. If the router is allowlisted (a natural admin configuration for pools that want to support router-mediated swaps), every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through the router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → MetricOmmPool.swap(): _beforeSwap(msg.sender=Router, ...)
     → ExtensionCalling._beforeSwap(sender=Router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=Router, ...)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the swap originates from the router: [3](#0-2) 

The router calls the pool directly with no mechanism to forward the original user's identity: [4](#0-3) 

**Contrast with `DepositAllowlistExtension`:** the deposit guard correctly checks `owner` (the economic beneficiary, explicitly passed by the caller), not `sender` (the payer/operator). The swap guard has no equivalent field — the pool's `swap()` signature carries no "original user" argument, so the extension can only see the direct caller. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address (so that allowlisted users can conveniently use the router) inadvertently opens the gate to **all** users. Any address — including those explicitly excluded from the allowlist — can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and the extension will pass because it sees `allowedSwapper[pool][router] == true`. The curated pool's access control is completely nullified. This is a direct loss-of-policy impact: trades that should be rejected execute at live oracle prices, draining LP value to unauthorized counterparties.

The inverse also holds: if the admin does not allowlist the router, allowlisted users cannot use the router at all, breaking core swap functionality for the intended audience.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap entrypoint in the periphery package and is expected to be used by end users. A pool admin who wants to support router-mediated swaps for allowlisted users has no choice but to allowlist the router address, which immediately triggers the bypass. The configuration is natural and the bypass requires no special privileges — any EOA can call the router.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic actor**, not the direct pool caller. Two complementary fixes:

1. **Add a recipient/originator field to the swap extension interface** — pass the original initiator (e.g., the router's `msg.sender`) through `extensionData` or a dedicated parameter so the extension can check it. The router already has access to `msg.sender` at entry and can encode it into `extensionData`.

2. **Alternatively, gate by `recipient`** — for router swaps the recipient is the user-controlled address. This is weaker (recipient can be set to any address) but avoids interface changes.

3. **Document the limitation clearly** — if the interface is not changed, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and that pools using it must require direct pool calls only.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker EOA

Attack:
  - attacker (not on allowlist) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: curated_pool,
          tokenIn: token0,
          zeroForOne: true,
          amountIn: X,
          amountOutMinimum: 0,
          ...
      })

Result:
  - Pool.swap() is called with msg.sender = router
  - SwapAllowlistExtension.beforeSwap(sender=router, ...) checks allowedSwapper[pool][router] == true → passes
  - Swap executes at live oracle price
  - Attacker receives token1 output; curated pool's allowlist policy is bypassed
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-83)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
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
