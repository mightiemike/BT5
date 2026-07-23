### Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the pool admin allowlists the router (the only way to permit router-based swaps on a curated pool), every user — including those not individually allowlisted — can bypass the per-user gate by going through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is used, it calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

The pool therefore sets `sender = address(router)`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The actual user (`msg.sender` of the router call) is stored only in transient storage as the payer and is never surfaced to the extension.

**Contrast with `DepositAllowlistExtension`**: the deposit guard checks `owner` (the second parameter), which is the user-specified position owner passed explicitly through the call chain — correctly identifying the economic actor even when the `MetricOmmPoolLiquidityAdder` is the direct caller: [5](#0-4) 

The swap guard has no equivalent: the pool's `swap()` interface exposes only `sender` (direct caller) and `recipient` (output receiver), neither of which reliably identifies the originating user when a router is in the path.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of addresses is fully bypassed. Any user — including those explicitly not allowlisted — can execute swaps by calling `MetricOmmSimpleRouter` instead of the pool directly, provided the router is allowlisted. The pool receives real token input and delivers real token output; the allowlist policy produces no effect. This constitutes a broken core pool functionality and a direct admin-boundary break: the pool admin's curation policy is silently voided.

---

### Likelihood Explanation

For router-based swaps to work at all on a curated pool, the pool admin **must** either set `allowAllSwappers = true` (which defeats the allowlist entirely) or explicitly allowlist the router address. Allowlisting the router is the natural operational choice for any pool that wants to support the standard periphery. Once the router is allowlisted, the bypass is unconditional and requires no special privileges — any EOA can call `exactInputSingle` or `exactInput`. The router is a deployed, publicly accessible contract.

---

### Recommendation

The swap allowlist must gate the **originating user**, not the direct pool caller. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Mirror the deposit pattern**: Introduce an explicit `swapper` parameter to `pool.swap()` (analogous to `owner` in `addLiquidity`) that the router fills with `msg.sender`. The pool passes it to the extension as a distinct field, separate from `sender`. The extension checks that field instead of `sender`.

Until fixed, pools that rely on `SwapAllowlistExtension` for access control should not allowlist the router address and should document that only direct pool calls are gated.

---

### Proof of Concept

**Setup**: Deploy a pool with `SwapAllowlistExtension`. Set `allowAllSwappers = false`. Allowlist the router address so that router-based swaps are permitted. Do **not** allowlist `attacker`.

**Attack**:
```
attacker (not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(pool, recipient=attacker, ...)
      → pool.swap(recipient=attacker, ...)   [msg.sender = router]
          → _beforeSwap(sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  → allowedSwapper[pool][router] == true  ✓  (passes)
          → swap executes, attacker receives tokens
```

**Expected**: `NotAllowedToSwap` revert because `attacker` is not allowlisted.
**Actual**: Swap succeeds because the check is on the router address, which is allowlisted.

The pool admin's intent — restricting swaps to a curated set of addresses — is completely bypassed through the standard periphery router. [6](#0-5) [7](#0-6) [4](#0-3)

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
