### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of its own `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool is the **router contract**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router to support router-mediated swaps for legitimate users, every unprivileged address can bypass the curated allowlist by routing through the router.

This is the direct analog of the `_sendMessage` bug: the wrong address (the intermediary) is used in a security-critical check where the intended actor (the actual user) was required.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the `msg.sender` of the pool: [4](#0-3) 

The router never forwards the original caller's identity to the pool or to the extension. The extension has no mechanism to recover it.

**Contrast with `DepositAllowlistExtension`**, which correctly gates on `owner` (the economically relevant actor for deposits), not on `sender` (the intermediary): [5](#0-4) 

For swaps the economically relevant actor is the user who initiates and pays for the trade, but the extension sees only the router.

---

### Impact Explanation

A pool admin who wants to support both direct and router-mediated swaps for a curated set of users must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the check at line 37 passes for **every caller** of the router, regardless of whether that caller is individually allowlisted. The per-user curation is completely defeated. Any unprivileged address can swap on the curated pool by routing through `MetricOmmSimpleRouter`.

The secondary impact (if the router is not allowlisted) is that individually allowlisted users cannot use the router at all, breaking the core swap flow for the supported periphery path.

---

### Likelihood Explanation

- `SwapAllowlistExtension` is a production extension in the periphery, designed for curated pools.
- `MetricOmmSimpleRouter` is the official, supported swap entry point.
- A pool admin who configures both will naturally allowlist the router to avoid locking out legitimate users from the router path. This is the expected operational pattern and the step that triggers the bypass.
- No special permissions, flash loans, or non-standard tokens are required. Any EOA can call `exactInputSingle` on the router.

---

### Recommendation

Pass the original caller's identity through the swap path so the extension can gate on the actual user rather than the intermediary. Two viable approaches:

1. **Add an explicit `originator` field** to the pool's `swap` interface and have the router populate it with `msg.sender`. The extension checks `originator` instead of `sender`.
2. **Encode the caller in `extensionData`** at the router level and have `SwapAllowlistExtension` decode and verify it (requires a trusted-router assumption or a signature scheme).

As a short-term mitigation, document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must interact with the pool directly.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is the intended curated user
  allowedSwapper[pool][router] = true   // admin adds this so alice can use the router

Attack:
  charlie (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

  Router calls:
    pool.swap(recipient=charlie, ...)
    // msg.sender of pool = router

  Pool calls:
    _beforeSwap(sender=router, ...)

  SwapAllowlistExtension.beforeSwap checks:
    allowedSwapper[pool][router] == true  ✓  → swap proceeds

Result:
  Charlie, who is not individually allowlisted, successfully swaps on the
  curated pool. The allowlist is fully bypassed for any router caller.
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
