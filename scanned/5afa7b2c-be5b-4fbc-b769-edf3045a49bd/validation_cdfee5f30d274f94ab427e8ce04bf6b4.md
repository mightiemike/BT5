### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. However, when a user swaps through `MetricOmmSimpleRouter`, the pool receives the **router's address** as `msg.sender` and forwards it as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not whether the **actual end user** is allowlisted. Any unprivileged user can bypass a curated pool's swap allowlist by routing through the public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
              [msg.sender = router]
         → _beforeSwap(msg.sender=router, recipient, ...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks: allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards this directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls `pool.swap(...)` directly without forwarding the original user's identity: [4](#0-3) 

The pool admin intends to allowlist specific users (e.g., KYC'd traders). To also allow those users to use the router, the admin must allowlist the router address. But once the router is allowlisted, **every user** — including non-allowlisted ones — can call the router and pass the check, because the extension sees `sender = router` (allowlisted) rather than the actual caller.

Contrast this with `DepositAllowlistExtension`, which correctly gates by `owner` (the economically relevant actor for deposits), not by `sender`: [5](#0-4) 

The swap allowlist has no equivalent correct binding.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user executes swaps at oracle-anchored prices, draining LP value or extracting arbitrage that the allowlist was designed to prevent. This is a direct loss of LP principal and a complete curation failure on the affected pool.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented and deployed for the protocol. Any pool admin who allowlists the router (the natural step to let their allowlisted users use the standard UI) simultaneously opens the pool to all users. The trigger requires no special privilege: any EOA can call `exactInputSingle` or `exactInput` on the router. The bypass is reachable on every router-mediated swap against a pool with `SwapAllowlistExtension` configured.

---

### Recommendation

The extension must gate by the **original user**, not the intermediary. Two options:

1. **Pass the original caller through the router.** The router stores `msg.sender` in transient storage (already done for the payer context) and the pool exposes it to extensions. This requires a protocol-level change to the `sender` semantics.

2. **Check `sender` only when `sender` is not a known periphery contract; otherwise revert.** This is fragile and not recommended.

The cleanest fix is for `SwapAllowlistExtension` to be documented as **direct-call only** (not compatible with the router), and for the router to be explicitly excluded from pools that use it — or for the pool to pass the original initiator through a separate field in the extension call.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)
    → intended to let allowlisted users reach the pool via the router
  pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: attacker, ...})

  router calls:
    pool.swap(attacker, zeroForOne, amount, priceLimit, "", extensionData)
    [msg.sender in pool = router]

  pool calls:
    _beforeSwap(sender=router, ...)

  extension checks:
    allowedSwapper[pool][router] == true  ✓  (bypass succeeds)

  attacker receives swap output — allowlist completely bypassed.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
